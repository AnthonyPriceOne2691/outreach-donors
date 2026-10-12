"""Проход судьи сегмента по кандидатам прогона на настоящей базе: кэш вердиктов, выключенный
судья, партии со строкой расхода, сбой одного сайта.

Подделки выдачи, модели и сайтов — те же, что у прогона целиком (`test_sales_segment_run.py`).
"""

from __future__ import annotations

import httpx
import pytest
from backend.config.judge import JudgeMode
from backend.features.core.domain import UsageProvider
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.sales import segment_judging
from backend.features.sales.segment_judge import (
    JUDGE_OFF,
    PROMPT_VERSION,
    SegmentClient,
    segment_digest,
)
from backend.features.sales.segment_judging import judge_run
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentVerdictModel,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_segment_judge import MODEL_KEY
from tests.test_sales_segment_run import (
    ANSWERS,
    NEWS,
    PLATFORM,
    SEGMENT,
    SHOP,
    TITLES,
    Model,
    hypothesis,
    make_run,
)

Candidate = SalesSegmentCandidateModel


async def candidates(
    session: AsyncSession, run_id: int, owner_id: int, *hosts: str
) -> list[Candidate]:
    """Кандидаты прогона, как их заводит выдача: текст страницы и «ещё не судили»."""
    made = []
    for host in hosts:
        domain = await session.scalar(select(DomainModel).where(DomainModel.host == host))
        if domain is None:
            domain = DomainModel(host=host)
            session.add(domain)
            await session.flush()
        made.append(
            Candidate(
                run_id=run_id,
                hypothesis_id=owner_id,
                domain_id=domain.id,
                page_title=TITLES.get(host, host),
                page_description=f"О сайте {host}",
                lane=Lane.PENDING,
            )
        )
    session.add_all(made)
    await session.commit()
    return made


def judge(model: Model) -> SegmentClient:
    return SegmentClient(
        httpx.AsyncClient(transport=httpx.MockTransport(model)),
        model="gpt-5-mini",
        api_key=MODEL_KEY,
    )


async def test_a_fresh_verdict_of_the_same_prompt_and_segment_costs_no_tokens(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    [shop] = await candidates(session, run.id, owner.id, SHOP)
    session.add(
        SalesSegmentVerdictModel(
            domain_id=shop.domain_id,
            hypothesis_id=owner.id,
            prompt_version=PROMPT_VERSION,
            segment_digest=segment_digest(SEGMENT),
            verdict=Lane.ACCEPT,
            quote="интернет-магазин Велошоп",
            reason="магазин велосипедов",
            model="gpt-5-mini",
            decided_by=Decider.MODEL,
            tokens=613,
        )
    )
    await session.commit()
    model = Model(ANSWERS)

    tally = await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(model),
        home=None,
    )

    assert model.asked == []
    assert (tally.from_cache, tally.judged, tally.tokens) == (1, 0, 0)
    assert (shop.lane, shop.quote) == (Lane.ACCEPT, "интернет-магазин Велошоп")


async def test_another_segment_description_is_a_new_question(session: AsyncSession) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    [shop] = await candidates(session, run.id, owner.id, SHOP)
    session.add(
        SalesSegmentVerdictModel(
            domain_id=shop.domain_id,
            hypothesis_id=owner.id,
            prompt_version=PROMPT_VERSION,
            segment_digest=segment_digest("Другой сегмент."),
            verdict=Lane.REJECT,
            reason="не тот сегмент",
            decided_by=Decider.MODEL,
        )
    )
    await session.commit()
    model = Model(ANSWERS)

    await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(model),
        home=None,
    )

    assert model.asked == [SHOP]
    assert shop.lane == Lane.ACCEPT
    count = await session.scalar(select(func.count()).select_from(SalesSegmentVerdictModel))
    assert count == 2  # прежний вердикт остался — по нему меряется прежняя версия вопроса


async def test_a_switched_off_judge_asks_nobody_and_hands_every_site_to_a_human(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner, mode=JudgeMode.OFF)
    shop, platform = await candidates(session, run.id, owner.id, SHOP, PLATFORM)
    model = Model(ANSWERS)

    await judge_run(
        session, run_id=run.id, segment=SEGMENT, mode=JudgeMode.OFF, client=judge(model), home=None
    )

    assert model.asked == []
    assert (shop.lane, shop.reason, shop.verdict) == (Lane.REVIEW, JUDGE_OFF, None)
    # Правило по домену бесплатно и режет и без модели.
    assert (platform.lane, platform.decided_by) == (Lane.REJECT, Decider.RULE)


async def test_a_switched_off_judge_does_not_let_a_cached_verdict_decide(
    session: AsyncSession,
) -> None:
    """Выключенный судья — решает человек: прежний «принять» модели из кэша сайт не принимает."""
    owner = await hypothesis(session)
    run = await make_run(session, owner, mode=JudgeMode.OFF)
    [shop] = await candidates(session, run.id, owner.id, SHOP)
    session.add(
        SalesSegmentVerdictModel(
            domain_id=shop.domain_id,
            hypothesis_id=owner.id,
            prompt_version=PROMPT_VERSION,
            segment_digest=segment_digest(SEGMENT),
            verdict=Lane.ACCEPT,
            quote="интернет-магазин Велошоп",
            reason="магазин велосипедов",
            decided_by=Decider.MODEL,
        )
    )
    await session.commit()

    await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.OFF,
        client=judge(Model(ANSWERS)),
        home=None,
    )

    assert (shop.lane, shop.reason) == (Lane.REVIEW, JUDGE_OFF)


async def test_each_batch_writes_its_own_spending_line(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(segment_judging, "BATCH", 2)
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    hosts = [f"velo-{n}-sample.com" for n in ("one", "two", "three")]
    await candidates(session, run.id, owner.id, *hosts)
    model = Model(dict.fromkeys(hosts, ANSWERS[NEWS]), tokens=401)

    tally = await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(model),
        home=None,
    )

    lines = (
        await session.scalars(
            select(UsageRecordModel.units)
            .where(
                UsageRecordModel.operation == "sales_segment_judge",
                UsageRecordModel.provider == UsageProvider.LLM,
            )
            .order_by(UsageRecordModel.id)
        )
    ).all()
    assert list(lines) == [2 * 401, 401]
    assert tally.tokens == 3 * 401


async def test_one_site_failing_does_not_stop_the_others(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    shop, news = await candidates(session, run.id, owner.id, SHOP, NEWS)
    real = segment_judging._one

    async def broken(candidate: Candidate, host: str, **kwargs: object) -> object:
        if host == NEWS:
            raise RuntimeError("ошибка кода")
        return await real(candidate, host, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(segment_judging, "_one", broken)

    tally = await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(Model(ANSWERS)),
        home=None,
    )

    assert (shop.lane, news.lane, tally.failed) == (Lane.ACCEPT, Lane.PENDING, 1)


async def test_a_human_decision_on_an_unanswered_site_is_not_judged_over(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    [shop] = await candidates(session, run.id, owner.id, SHOP)
    shop.lane, shop.unanswered, shop.decided_by = Lane.REJECT, True, Decider.HUMAN
    await session.commit()
    model = Model(ANSWERS)

    await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(model),
        home=None,
    )

    assert model.asked == []
    assert shop.lane == Lane.REJECT
