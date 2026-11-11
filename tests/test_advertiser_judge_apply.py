"""Советы судьи рекламодателей — решениями: отбор, план, порции, гонка с человеком и замер.

Проверяется то, чем применение может навредить молча: применить совет прежней подсказки или не тот,
что последний; тронуть «не понять» и пары без совета; затереть решение человека, пришедшее между
планом и порцией; потерять или задвоить пары на обрыве; провести к письму сайт без DR; дать судье
мерить самого себя в доле ложных. Модель не зовётся: строки суда заведены как после прохода.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.config import ahrefs as ahrefs_cfg
from backend.config import llm as llm_cfg
from backend.features.core.domain import MessageStatus, Verdict
from backend.features.core.models.advertiser import AdvertiserJudgementModel, CandidateModel
from backend.features.crawl import judge_apply, judge_plan, review, strata
from backend.features.crawl.accuracy import scoring_check
from backend.features.crawl.judge_apply import Band, BatchBandError, Standing
from backend.features.crawl.judge_facts import LinkFacts
from backend.features.crawl.judge_mark import BATCH, batch_of, marker_of, started_at
from backend.features.crawl.judge_rules import RULES_VERSION, Advice, advise
from backend.features.crawl.judge_store import cohort_of
from backend.features.crawl.promote import promote
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.stage2_rows import addressed, answered, crawled, lettered, promoted, scored
from tests.test_advertiser_judge import facts_body
from tests.test_scoring_accuracy import hosts, turn_order

DONOR = "donor.example.test"
HUMAN = "человек@site.example.test"
SKIP_BATCH = "skip-20261101-090000-a1b2"
WRITE_BATCH = "write-20261101-090000-c3d4"

#: Факты, по которым нынешняя таблица говорит «не пишем» и «пишем»: переадресация и бизнес, которого
#: ссылка продвигает. Тесту важен совет в строке, а слова примеров берутся по этим фактам.
FACTS: dict[str, dict[str, Any]] = {
    "skip": {"target_redirect": True, "verdict": "skip"},
    "write": {},
    "unclear": {"verdict": "unclear"},
}
RULE = {"skip": "redirect", "write": "promoted", "unclear": "rest"}


def judged(
    target: str,
    advice: str,
    *,
    donor: str = DONOR,
    context: str = "c",
    days_ago: float = 1,
    **changes: Any,
) -> AdvertiserJudgementModel:
    """Строка суда пары нынешней когортой и нынешней таблицей; `changes` — что в ней не так."""
    cohort = cohort_of(llm_cfg.ADVERTISER_JUDGE_MODEL)
    values: dict[str, Any] = {
        "donor_host": donor,
        "target_root": target,
        "context_print": context * 64,
        "prompt_version": cohort.prompt_version,
        "prompt_print": cohort.prompt_print,
        "examples_print": cohort.examples_print,
        "model": cohort.model,
        "rules_version": RULES_VERSION,
        "facts": facts_body(**FACTS[advice]),
        "advice": advice,
        "rule": RULE[advice],
        "seen": "Абзац со ссылкой: …",
        "tokens": 100,
        "retried": False,
        "updated_at": datetime.now(UTC) - timedelta(days=days_ago),
    }
    return AdvertiserJudgementModel(**(values | changes))


async def disputed(session: AsyncSession, *advised: tuple[str, str | None]) -> dict[str, int]:
    """Спорные пары донора с советом судьи каждой (`None` — без суда): домен → номер кандидата."""
    run = await crawled(session, DONOR)
    ids: dict[str, int] = {}
    for target, advice in advised:
        ids[target] = (await scored(session, run, target, verdict=Verdict.PENDING)).id
        if advice is not None:
            session.add(judged(target, advice))
    await session.flush()
    return ids


async def _row(session: AsyncSession, candidate_id: int) -> CandidateModel:
    found = await session.get(CandidateModel, candidate_id, populate_existing=True)
    assert found is not None
    return found


# --- метка -----------------------------------------------------------------------------------------


def test_the_marker_names_the_batch_and_only_a_judge_has_one() -> None:
    batch = judge_apply.new_batch(Band.SKIP, now=datetime(2026, 11, 1, 9, 30, 15, tzinfo=UTC))

    assert BATCH.fullmatch(batch) is not None
    assert batch.startswith("skip-20261101-093015-")
    assert batch_of(marker_of(batch)) == batch
    assert len(marker_of(batch)) <= 128
    assert batch_of(HUMAN) is None
    assert batch_of(None) is None
    assert judge_apply.band_of(batch) is Band.SKIP
    assert started_at(batch) == datetime(2026, 11, 1, 9, 30, 15, tzinfo=UTC)


@pytest.mark.parametrize(
    "name",
    [
        "unclear-20261101-093015-a1b2",  # «не понять» полосой не бывает
        "skip-20261341-093015-a1b2",  # такой даты нет
        "skip-29991101-093015-a1b2",  # из будущего: по времени в имени журнал ищет партию
        "skip 20261101-093015-a1b2",
    ],
)
def test_a_batch_named_not_the_way_the_service_names_them_is_refused(name: str) -> None:
    with pytest.raises(BatchBandError):
        judge_apply.band_of(name)


# --- отбор -----------------------------------------------------------------------------------------


async def test_a_band_takes_the_latest_advice_of_the_cohort_in_force(
    session: AsyncSession,
) -> None:
    """Каждая пара — ровно в одном числе; в полосу идёт только последний совет нынешней когорты."""
    ids = await disputed(
        session,
        ("skip.example.test", "skip"),
        ("write.example.test", "write"),
        ("unclear.example.test", "unclear"),
        ("unjudged.example.test", None),
        ("old-prompt.example.test", None),
        ("changed.example.test", "write"),
        ("old-model.example.test", "skip"),
        ("expired.example.test", None),
    )
    session.add_all(
        [
            # Суд прежней подсказкой — не совет: его обновит «Переобойти спорные».
            judged("old-prompt.example.test", "skip", prompt_version="advertiser-judge-v0"),
            # Позже статью перечитали — новый отпечаток и другой совет: в силе последний.
            judged("changed.example.test", "skip", context="d", days_ago=0.5),
            # Суд другой моделью позже нынешнего его не отменяет: свежесть — по когорте, как у прохода.
            judged("old-model.example.test", "write", model="another-model", days_ago=0.1),
            # Срок фактов вышел: проход спросит заново, а до того совета нет.
            judged("expired.example.test", "skip", days_ago=400),
        ]
    )
    run = await crawled(session, "second-donor.example.test")
    await scored(session, run, "decided.example.test", verdict=Verdict.PENDING, confirmed=True)
    await scored(session, run, "bought.example.test", verdict=Verdict.BOUGHT)
    session.add_all(
        [
            judged("decided.example.test", "skip", donor=run.host),
            judged("bought.example.test", "skip", donor=run.host),
        ]
    )
    await session.flush()

    rows = {one.target_root: one for one in await judge_apply.said(session)}

    assert set(rows) == set(ids), "решённые и «куплено» в отбор не входят"
    assert {root for root, one in rows.items() if one.fits(Band.SKIP)} == {
        "skip.example.test",
        "changed.example.test",
        "old-model.example.test",
    }
    assert {root for root, one in rows.items() if one.fits(Band.WRITE)} == {"write.example.test"}
    assert {root for root, one in rows.items() if one.stale} == {
        "old-prompt.example.test",
        "expired.example.test",
    }
    assert judge_apply.standing_of(list(rows.values())) == Standing(
        waiting=8, skip=3, write=1, unclear=1, stale=2, without=1
    )


async def test_advice_set_by_an_older_table_is_relabelled_before_the_choice(
    session: AsyncSession,
) -> None:
    """Таблицу правили, а пересчёт советов запускать некому: строка с прежней версией таблицы до
    отбора получает совет нынешней по своим фактам — без модели."""
    ids = await disputed(session, ("relabelled.example.test", None), ("broken.example.test", None))
    facts = facts_body(**FACTS["skip"])
    expected = advise(LinkFacts.from_json(facts))  # type: ignore[arg-type]
    assert expected.advice in (Advice.SKIP, Advice.WRITE)
    stale = judged(
        "relabelled.example.test",
        "unclear",
        facts=facts,
        rule="rest",
        rules_version="advertiser-rules-v0",
    )
    # Факты не той формы пересчёту не даются: совет остаётся устаревшим и не применяется.
    broken = judged("broken.example.test", "skip", facts={}, rules_version="advertiser-rules-v0")
    session.add_all([stale, broken])
    await session.flush()
    judged_at = stale.updated_at

    plan = await judge_plan.plan(session, Band(expected.advice.value))

    await session.refresh(stale)
    assert (stale.advice, stale.rule, stale.rules_version) == (
        expected.advice.value,
        expected.rule,
        RULES_VERSION,
    )
    assert stale.updated_at == judged_at, "срок фактов — их, а не пересчёта"
    assert plan.pairs == 1
    assert [one.target_root for one in plan.examples] == ["relabelled.example.test"]
    assert plan.standing.stale == 1
    await session.refresh(broken)
    assert broken.rules_version == "advertiser-rules-v0"
    assert set(ids) == {"relabelled.example.test", "broken.example.test"}


async def test_writing_waits_for_the_rating_and_skipping_does_not(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«Пишем» человека проводит пару мимо DR; судья DR не знает — пара без него ждёт."""
    monkeypatch.setattr(ahrefs_cfg, "API_KEY", "test-key")
    ids = await disputed(
        session,
        ("rated.example.test", "write"),
        ("unrated.example.test", "write"),
        ("unrated-skip.example.test", "skip"),
    )
    (await _row(session, ids["rated.example.test"])).dr_checked_at = datetime.now(UTC)
    await session.flush()

    rows = await judge_apply.said(session)
    applied = await judge_apply.apply(session, Band.WRITE, batch=WRITE_BATCH)

    assert judge_apply.standing_of(rows) == Standing(waiting=3, skip=1, write=1, waiting_dr=1)
    assert (applied.applied, applied.left) == (1, 0)
    assert (await _row(session, ids["rated.example.test"])).confirmed is True
    assert (await _row(session, ids["unrated.example.test"])).confirmed is None


# --- план ------------------------------------------------------------------------------------------


async def test_the_plan_counts_rules_and_shows_examples_by_lot(session: AsyncSession) -> None:
    targets = [f"site-{number}.example.test" for number in range(30)]
    await disputed(session, *((target, "skip") for target in targets))
    second = await crawled(session, "second-donor.example.test")
    await scored(session, second, "navigation.example.test", verdict=Verdict.PENDING, points=3)
    session.add(judged("navigation.example.test", "skip", donor=second.host, rule="navigation"))
    await session.flush()

    plan = await judge_plan.plan(session, Band.SKIP)

    assert (plan.pairs, plan.donors) == (31, 2)
    assert [(one.rule, one.pairs) for one in plan.rules] == [("redirect", 30), ("navigation", 1)]
    assert all(one.words and one.words != one.rule for one in plan.rules)
    assert len(plan.examples) == judge_plan.EXAMPLES
    shown = [one.target_root for one in plan.examples]
    by_lot = sorted(
        [*targets, "navigation.example.test"],
        key=lambda root: hashlib.md5(root.encode(), usedforsecurity=False).hexdigest(),
    )
    assert shown == by_lot[: judge_plan.EXAMPLES], "примеры — жребием, а не верхом очереди"
    assert all(one.words for one in plan.examples)
    assert (plan.takes_off, plan.launch) == (0, None)
    # План решений не пишет.
    assert (await review.waiting(session)) == 31


async def test_the_plan_of_writing_says_what_the_promotion_gets(session: AsyncSession) -> None:
    """Сколько доменов станут рекламодателями после «Перевести» и у скольких адрес уже есть."""
    await disputed(
        session,
        ("fresh.example.test", "write"),
        ("addressed.example.test", "write"),
        ("known.example.test", "write"),
    )
    await addressed(session, "addressed.example.test")
    await promoted(session, "known.example.test")
    second = await crawled(session, "second-donor.example.test")
    await scored(session, second, "fresh.example.test", verdict=Verdict.PENDING)
    session.add(judged("fresh.example.test", "write", donor=second.host))
    await session.flush()

    plan = await judge_plan.plan(session, Band.WRITE)

    assert plan.pairs == 4
    assert plan.launch == judge_plan.Launch(domains=3, fresh=2, with_address=1)


async def test_the_plan_of_skipping_names_the_advertisers_it_takes_off(
    session: AsyncSession,
) -> None:
    """Рекламодатель остался от ссылки, которую обход больше не находит: «не пишем» по спорной его
    снимет — план говорит это до нажатия. Домен, которому пишем и по другой ссылке, остаётся."""
    await disputed(session, ("orphan.example.test", "skip"), ("held.example.test", "skip"))
    await promoted(session, "orphan.example.test")
    await promoted(session, "held.example.test")
    second = await crawled(session, "second-donor.example.test")
    await scored(session, second, "held.example.test", verdict=Verdict.BOUGHT)

    plan = await judge_plan.plan(session, Band.SKIP)
    applied = await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH)

    assert plan.takes_off == 1
    assert (applied.applied, applied.removed) == (2, 1)


# --- применение ------------------------------------------------------------------------------------


async def test_skipping_decides_the_band_the_way_a_human_does(session: AsyncSession) -> None:
    ids = await disputed(
        session,
        ("skip.example.test", "skip"),
        ("write.example.test", "write"),
        ("unclear.example.test", "unclear"),
        ("unjudged.example.test", None),
    )

    result = await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH)

    assert (result.applied, result.decided, result.gone, result.left) == (1, 0, 0, 0)
    decided = await _row(session, ids["skip.example.test"])
    assert (decided.confirmed, decided.decided_by) == (False, marker_of(SKIP_BATCH))
    assert decided.decided_at is not None
    assert decided.points == 2, "балл скоринга решение не переписывает"
    for untouched in ("write.example.test", "unclear.example.test", "unjudged.example.test"):
        assert (await _row(session, ids[untouched])).confirmed is None
    # Решённое не теряется: его нет в очереди, но оно видно под «Показывать решённые».
    waiting = {row.target_root for row in (await review.queue(session)).rows}
    every = {row.target_root for row in (await review.queue(session, include_decided=True)).rows}
    assert "skip.example.test" not in waiting
    assert "skip.example.test" in every


async def test_writing_makes_the_pair_eligible_for_the_promotion(session: AsyncSession) -> None:
    ids = await disputed(session, ("write.example.test", "write"), ("skip.example.test", "skip"))

    result = await judge_apply.apply(session, Band.WRITE, batch=WRITE_BATCH)
    report = await promote(session)

    assert (result.applied, result.left) == (1, 0)
    decided = await _row(session, ids["write.example.test"])
    assert (decided.confirmed, decided.decided_by) == (True, marker_of(WRITE_BATCH))
    assert (await _row(session, ids["skip.example.test"])).confirmed is None
    assert report.hosts == ["write.example.test"], "рекламодателем пару делает перевод, не судья"


async def test_a_batch_of_one_band_is_not_continued_by_another(session: AsyncSession) -> None:
    await disputed(session, ("write.example.test", "write"))

    with pytest.raises(BatchBandError):
        await judge_apply.apply(session, Band.WRITE, batch=SKIP_BATCH)

    assert await review.waiting(session) == 1


async def test_a_pair_decided_meanwhile_is_skipped_and_counted(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Между отбором и порцией человек решил одну пару, а новый обход заменил строку другой:
    решение человека не затёрто, обе названы числом."""
    ids = await disputed(
        session,
        ("human.example.test", "skip"),
        ("judge.example.test", "skip"),
        ("replaced.example.test", "skip"),
    )
    chosen = await judge_apply.said(session)
    await review.decide(session, ids["human.example.test"], confirmed=True, by=HUMAN)
    await session.delete(await _row(session, ids["replaced.example.test"]))
    await session.flush()

    async def chosen_before(_session: AsyncSession, **_: Any) -> list[judge_apply.Said]:
        return chosen

    monkeypatch.setattr(judge_apply, "said", chosen_before)

    result = await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH)

    assert (result.applied, result.decided, result.gone) == (1, 1, 1)
    human = await _row(session, ids["human.example.test"])
    assert (human.confirmed, human.decided_by) == (True, HUMAN)
    assert (await _row(session, ids["judge.example.test"])).confirmed is False


async def test_portions_go_on_from_where_a_broken_one_stopped(session: AsyncSession) -> None:
    """Порции с фиксацией между ними: обрыв теряет только летевшую, повтор продолжает с места — ни
    одна пара не решена дважды и ни одна не потеряна."""
    targets = [f"site-{number}.example.test" for number in range(5)]
    ids = await disputed(session, *((target, "skip") for target in targets))

    first = await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH, limit=2)
    await session.commit()
    with pytest.raises(RuntimeError):
        async with session.begin_nested():
            await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH, limit=2)
            raise RuntimeError("обрыв посреди порции")
    assert await review.waiting(session) == 3, "летевшая порция откатилась целиком"
    second = await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH, limit=2)
    await session.commit()
    third = await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH, limit=2)

    assert [(one.applied, one.left) for one in (first, second, third)] == [(2, 3), (2, 1), (1, 0)]
    rows = [await _row(session, number) for number in ids.values()]
    assert {row.decided_by for row in rows} == {marker_of(SKIP_BATCH)}
    assert await review.waiting(session) == 0


async def test_skipping_says_who_has_already_answered(session: AsyncSession) -> None:
    """Предупреждение решения человека — «уже ответил, переписка идёт» — доходит и из порции."""
    await disputed(session, ("talking.example.test", "skip"), ("quiet.example.test", "skip"))
    await promoted(session, "talking.example.test")
    await answered(session, await lettered(session, "talking.example.test", MessageStatus.SENT))

    result = await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH)

    assert result.applied == 2
    assert result.answered == ["talking.example.test"]


# --- замер -----------------------------------------------------------------------------------------


async def test_the_judge_does_not_measure_itself(session: AsyncSession) -> None:
    """Доля ложных — по решениям человека: строка выборки, решённая судьёй, для замера не решена —
    ни в доле, ни в «решено человеком», ни в срезах; переигранная человеком — его решение."""
    sample = turn_order(hosts(in_sample=True, number=4, prefix="sample"))
    run = await crawled(session, DONOR)
    rows = [await scored(session, run, root, verdict=Verdict.PENDING) for root in sample]
    session.add_all([judged(root, "skip") for root in sample[1:]])
    await review.decide(session, rows[0].id, confirmed=True, by=HUMAN)
    await session.flush()

    applied = await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH)
    check = await scoring_check(session)

    assert applied.applied == 3, "строки выборки судья решает наравне с остальными"
    disputed_share = check.disputed
    assert (disputed_share.decisions.decided, disputed_share.decisions.skip) == (1, 0)
    assert disputed_share.aside == 0
    assert disputed_share.share == 0.0
    pending = check.of(Verdict.PENDING)
    assert (pending.total, pending.write, pending.skip) == (4, 1, 0)
    by_points = (await strata.disputed_strata(session)).points
    assert [(one.decisions.write, one.decisions.skip) for one in by_points] == [(1, 0)]

    # Человек переиграл решение судьи по следующей строке выборки — оно идёт в замер как любое.
    await review.decide(session, rows[1].id, confirmed=False, by=HUMAN, force=True)
    again = await scoring_check(session)

    assert (again.disputed.decisions.decided, again.disputed.decisions.skip) == (2, 1)
    assert again.of(Verdict.PENDING).skip == 1


async def test_the_queue_page_still_counts_judge_decisions_as_decided(
    session: AsyncSession,
) -> None:
    """«Ждут человека» и «решено» — про очередь, а не про замер: решённое судьёй из очереди ушло."""
    ids = await disputed(
        session, ("skip.example.test", "skip"), ("unclear.example.test", "unclear")
    )

    await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH)

    assert await review.waiting(session) == 1
    counted = await session.scalar(
        select(CandidateModel.decided_by).where(CandidateModel.id == ids["skip.example.test"])
    )
    assert batch_of(counted) == SKIP_BATCH
