"""Откат партии советов судьи и память решений через новый обход.

Проверяется то, чем откат может соврать: тронуть решение, которое переиграл человек; снять
рекламодателя, которому письмо уже ушло; оставить в очереди письмо под откатанное «пишем»; вернуть
пару в очередь до первого ночного обхода, который достанет решение судьи из памяти. И обратное:
решение судьи переживает обход так же, как решение человека.
"""

from __future__ import annotations

import pytest
from backend.features.core.domain import MessageStatus, Verdict
from backend.features.core.models.advertiser import CandidateMemoryModel, CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.crawl import judge_apply, judge_revert, review
from backend.features.crawl.judge_apply import Band
from backend.features.crawl.judge_mark import marker_of
from backend.features.crawl.judge_revert import NothingBackError
from backend.features.crawl.promote import promote
from backend.features.letters.attempts import LINK_WITHDRAWN
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.stage2_rows import crawled, lettered, scored
from tests.test_advertiser_judge_apply import HUMAN, SKIP_BATCH, WRITE_BATCH, disputed, judged
from tests.test_crawl_decision_memory import DONOR as CRAWLED_DONOR
from tests.test_crawl_decision_memory import MAYBE, SOURCE, _Ahrefs, _candidate, _crawl, _memory

OTHER_DONOR = "second-donor.example.test"


async def _row(session: AsyncSession, candidate_id: int) -> CandidateModel:
    found = await session.get(CandidateModel, candidate_id, populate_existing=True)
    assert found is not None
    return found


async def _advertiser(session: AsyncSession, host: str) -> AdvertiserModel | None:
    return await session.scalar(
        select(AdvertiserModel)
        .join(DomainModel, DomainModel.id == AdvertiserModel.domain_id)
        .where(DomainModel.host == host)
    )


# --- «не пишем» ------------------------------------------------------------------------------------


async def test_a_reverted_batch_is_back_in_the_queue(session: AsyncSession) -> None:
    ids = await disputed(
        session,
        ("one.example.test", "skip"),
        ("two.example.test", "skip"),
        ("unclear.example.test", "unclear"),
    )
    await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH)
    assert await review.waiting(session) == 1

    result = await judge_revert.revert(session, SKIP_BATCH)

    assert (result.returned, result.kept, result.forgotten) == (2, 0, 0)
    assert await review.waiting(session) == 3
    for target in ("one.example.test", "two.example.test"):
        row = await _row(session, ids[target])
        assert (row.confirmed, row.decided_by, row.decided_at) == (None, None, None)
    # Совет судьи никуда не делся: полосу можно применить снова.
    again = await judge_apply.apply(session, Band.SKIP, batch="skip-20261102-090000-e5f6")
    assert again.applied == 2


async def test_the_revert_leaves_what_a_human_has_overruled(session: AsyncSession) -> None:
    ids = await disputed(session, ("human.example.test", "skip"), ("judge.example.test", "skip"))
    await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH)
    await review.decide(session, ids["human.example.test"], confirmed=True, by=HUMAN, force=True)

    result = await judge_revert.revert(session, SKIP_BATCH)

    assert result.returned == 1
    human = await _row(session, ids["human.example.test"])
    assert (human.confirmed, human.decided_by) == (True, HUMAN)
    assert (await _row(session, ids["judge.example.test"])).confirmed is None


async def test_the_revert_touches_only_its_own_batch(session: AsyncSession) -> None:
    other = "skip-20261031-090000-0a0b"
    ids = await disputed(session, ("first.example.test", "skip"))
    await judge_apply.apply(session, Band.SKIP, batch=other)
    ids |= {"second.example.test": (await _second(session, "second.example.test", "skip")).id}
    await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH)

    result = await judge_revert.revert(session, SKIP_BATCH)

    assert result.returned == 1
    assert (await _row(session, ids["first.example.test"])).decided_by == marker_of(other)
    assert (await _row(session, ids["second.example.test"])).decided_by is None


async def _second(session: AsyncSession, target: str, advice: str) -> CandidateModel:
    """Спорная пара второго донора с советом судьи."""
    run = await crawled(session, OTHER_DONOR)
    row = await scored(session, run, target, verdict=Verdict.PENDING)
    session.add(judged(target, advice, donor=OTHER_DONOR))
    await session.flush()
    return row


async def test_nothing_to_revert_is_a_refusal_before_any_write(session: AsyncSession) -> None:
    ids = await disputed(session, ("one.example.test", "skip"))
    with pytest.raises(NothingBackError, match="уже возвращены"):
        await judge_revert.revert(session, SKIP_BATCH)

    await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH)
    await judge_revert.revert(session, SKIP_BATCH)
    with pytest.raises(NothingBackError):
        await judge_revert.revert(session, SKIP_BATCH)

    assert (await _row(session, ids["one.example.test"])).confirmed is None


# --- «пишем» ---------------------------------------------------------------------------------------


async def test_reverting_writing_takes_the_advertiser_and_the_queued_letter_off(
    session: AsyncSession,
) -> None:
    """«Пишем» перевели и собрали письмо, оно ещё в очереди: откат снимает рекламодателя и письмо
    тем же кодом, что «не пишем» (`promote.withdraw`), — письмо под откатанное решение не уйдёт."""
    ids = await disputed(
        session, ("promoted.example.test", "write"), ("plain.example.test", "write")
    )
    await judge_apply.apply(session, Band.WRITE, batch=WRITE_BATCH)
    await promote(session)
    letter = await lettered(session, "promoted.example.test", MessageStatus.QUEUED)
    # Вторую пару так и не перевели бы — снимем её рекламодателя, чтобы проверить откат «всухую».
    plain = await _advertiser(session, "plain.example.test")
    assert plain is not None
    await session.delete(plain)
    await session.flush()

    result = await judge_revert.revert(session, WRITE_BATCH)

    assert (result.returned, result.kept, result.removed, result.stopped) == (2, 0, 1, 1)
    assert await _advertiser(session, "promoted.example.test") is None
    await session.refresh(letter)
    assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, LINK_WITHDRAWN)
    for target in ids:
        assert (await _row(session, ids[target])).confirmed is None
    assert await review.waiting(session) == 2


async def test_writing_is_kept_where_the_letter_has_gone(session: AsyncSession) -> None:
    """Письмо ушло — вернуть пару в очередь значило бы снять рекламодателя посреди переписки:
    решение остаётся и называется числом."""
    ids = await disputed(
        session, ("written.example.test", "write"), ("waiting.example.test", "write")
    )
    await judge_apply.apply(session, Band.WRITE, batch=WRITE_BATCH)
    await promote(session)
    await lettered(session, "written.example.test", MessageStatus.SENT)

    result = await judge_revert.revert(session, WRITE_BATCH)

    assert (result.returned, result.kept) == (1, 1)
    kept = await _row(session, ids["written.example.test"])
    assert (kept.confirmed, kept.decided_by) == (True, marker_of(WRITE_BATCH))
    assert await _advertiser(session, "written.example.test") is not None
    assert (await _row(session, ids["waiting.example.test"])).confirmed is None
    assert await _advertiser(session, "waiting.example.test") is None
    # Второй откат вернуть уже ничего не может — и говорит почему.
    with pytest.raises(NothingBackError, match="письмо уже ушло"):
        await judge_revert.revert(session, WRITE_BATCH)


async def test_a_sent_letter_does_not_hold_a_pair_when_another_donor_keeps_the_advertiser(
    session: AsyncSession,
) -> None:
    """На домен ссылается и другой донор — «куплено»: рекламодатель останется и без пары партии,
    поэтому её решение откатывается, а ушедшее письмо никто не трогает."""
    ids = await disputed(session, ("shared.example.test", "write"))
    other = await crawled(session, OTHER_DONOR)
    await scored(session, other, "shared.example.test", verdict=Verdict.BOUGHT)
    await judge_apply.apply(session, Band.WRITE, batch=WRITE_BATCH)
    await promote(session)
    letter = await lettered(session, "shared.example.test", MessageStatus.SENT)

    result = await judge_revert.revert(session, WRITE_BATCH)

    assert (result.returned, result.kept, result.removed) == (1, 0, 0)
    assert (await _row(session, ids["shared.example.test"])).confirmed is None
    advertiser = await _advertiser(session, "shared.example.test")
    assert advertiser is not None
    assert advertiser.best_donor_host == OTHER_DONOR
    await session.refresh(letter)
    assert letter.status is MessageStatus.SENT


# --- память обходов --------------------------------------------------------------------------------


async def _judged_pair(session: AsyncSession, advice: str) -> None:
    session.add(judged(MAYBE, advice, donor=CRAWLED_DONOR))
    await session.flush()


async def test_a_judge_decision_survives_a_recrawl_like_a_human_one(session: AsyncSession) -> None:
    """«Не пишем» судьи → обход без домена → обход с ним: решение на месте вместе с меткой партии,
    и человек в очереди пару второй раз не видит."""
    provider = _Ahrefs()
    await _crawl(session, provider, [MAYBE, SOURCE], article=1)
    await _judged_pair(session, "skip")
    applied = await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH)
    when = (await _candidate(session, MAYBE)).decided_at
    assert applied.applied == 1

    await _crawl(session, provider, [SOURCE], article=2)
    await _crawl(session, provider, [MAYBE, SOURCE], article=3)

    back = await _candidate(session, MAYBE)
    assert back.best_page_url == f"https://{CRAWLED_DONOR}/post/3"
    assert (back.verdict, back.confirmed, back.decided_by, back.decided_at) == (
        Verdict.PENDING,
        False,
        marker_of(SKIP_BATCH),
        when,
    )
    assert (await review.queue(session)).rows == []
    assert (await judge_apply.said(session)) == []


async def test_a_reverted_decision_does_not_come_back_from_the_memory(
    session: AsyncSession,
) -> None:
    """Пересчёт читает память раньше строк и строку без решения поверх не кладёт: без чистки памяти
    следующий обход вернул бы откатанной паре решение судьи."""
    provider = _Ahrefs()
    await _crawl(session, provider, [MAYBE, SOURCE], article=1)
    await _judged_pair(session, "skip")
    await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH)
    await _crawl(session, provider, [MAYBE, SOURCE], article=2)
    assert (await _memory(session))[MAYBE].decided_by == marker_of(SKIP_BATCH)

    result = await judge_revert.revert(session, SKIP_BATCH)
    await _crawl(session, provider, [MAYBE, SOURCE], article=3)

    assert (result.returned, result.forgotten) == (1, 0)
    back = await _candidate(session, MAYBE)
    assert (back.confirmed, back.decided_by, back.decided_at) == (None, None, None)
    memory = (await _memory(session))[MAYBE]
    assert (memory.confirmed, memory.decided_by) == (None, None)
    assert memory.dr == 31, "оплаченный DR откат не трогает"
    assert [row.target_root for row in (await review.queue(session)).rows] == [MAYBE]


async def test_a_pair_the_last_crawl_missed_is_reverted_in_the_memory(
    session: AsyncSession,
) -> None:
    """Последний обход домена не нашёл — решение партии живёт только в памяти: откат снимает его
    там, и пара возвращается нерешённой, когда обход найдёт её снова."""
    provider = _Ahrefs()
    await _crawl(session, provider, [MAYBE, SOURCE], article=1)
    await _judged_pair(session, "skip")
    await judge_apply.apply(session, Band.SKIP, batch=SKIP_BATCH)
    await _crawl(session, provider, [SOURCE], article=2)

    result = await judge_revert.revert(session, SKIP_BATCH)
    await _crawl(session, provider, [MAYBE, SOURCE], article=3)

    assert (result.returned, result.forgotten) == (0, 1)
    assert (await _candidate(session, MAYBE)).confirmed is None


async def test_the_memory_of_a_kept_pair_stays(session: AsyncSession) -> None:
    """«Пишем», по которому письмо ушло, остаётся в силе — и в памяти тоже: следующий обход не
    вернёт пару в очередь."""
    ids = await disputed(
        session, ("written.example.test", "write"), ("waiting.example.test", "write")
    )
    await judge_apply.apply(session, Band.WRITE, batch=WRITE_BATCH)
    await promote(session)
    await lettered(session, "written.example.test", MessageStatus.SENT)
    # Память — как после пересчёта обхода: решение партии лежит в ней у обеих пар.
    for target in ids:
        row = await _row(session, ids[target])
        session.add(
            CandidateMemoryModel(
                donor_host=row.donor_host,
                target_root=target,
                confirmed=True,
                decided_by=row.decided_by,
                decided_at=row.decided_at,
            )
        )
    await session.flush()

    result = await judge_revert.revert(session, WRITE_BATCH)

    assert (result.returned, result.kept, result.forgotten) == (1, 1, 0)
    remembered = await session.execute(
        select(CandidateMemoryModel.target_root, CandidateMemoryModel.decided_by).execution_options(
            populate_existing=True
        )
    )
    assert dict(remembered.tuples().all()) == {
        "written.example.test": marker_of(WRITE_BATCH),
        "waiting.example.test": None,
    }
