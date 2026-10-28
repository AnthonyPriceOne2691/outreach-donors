"""Решение человека и оплаченный DR переживают повтор обхода (решение владельца 28.10.2026).

«Переобойти» стоит у последнего обхода каждого донора, и повтор — обычное дело: карта отдаёт
адреса от новых к старым, потолок срезает старые, и домен, найденный одним обходом, следующий
может не найти. Пока решение и DR жили только на строке кандидата, такой повтор стирал их,
а повтор, нашедший домен снова, возвращал его без решения: «куплено» заводило рекламодателя,
которому сказали «не пишем», письмо ему собиралось, а DR покупался второй раз.

Путь — на настоящей базе: обход → решение → повтор без домена → повтор с ним, а дальше
перевод, сборка писем, проверка оффера и журнал расхода — то, что видит человек.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.ahrefs.client import Response
from backend.features.ahrefs.units import UnitsCost, UsageCollector
from backend.features.core.domain import ContactSource, CrawlOutcome, StopReason, Verdict
from backend.features.core.models.advertiser import CandidateMemoryModel, CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.crawl import gate, review, sample
from backend.features.crawl.big_sites import ahrefs_ratings, journal
from backend.features.crawl.links import OutLink
from backend.features.crawl.promote import promote, standing
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.repository import save_crawl
from backend.features.letters.offer_standing import _only_rejected, _rejected_link
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import load_migration
from tests.test_letters_advertisers import build_offers, offers, priced_donor

DONOR = "donor-memory.example.test"
OTHER_DONOR = "other-donor.example.test"
#: «Куплено»: `rel=sponsored`.
BRAND = "brand.example.test"
#: «Спорно»: призыв к действию при `nofollow` — решает человек.
MAYBE = "maybe.example.test"
#: «Мимо»: редакционное упоминание — обход без двух других не пустой.
SOURCE = "source.example.test"
BY = "оператор@team.example.test"
#: DR, который знает провайдер: оба меньше порога — пишем.
KNOWN_DR = {BRAND: 23, MAYBE: 31}
MIGRATION = "77ff8a338610_candidate_memory.py"

#: Чем ссылка на домен похожа на свой вердикт: анкор, `sponsored`, `nofollow`.
SHAPES = {
    BRAND: ("Acme", True, False),
    MAYBE: ("Play now", False, True),
    SOURCE: ("a long editorial mention of the source", False, False),
}


class _Ahrefs:
    """Провайдер DR: помнит, о ком спросили, и сообщает расход, как настоящий клиент."""

    def __init__(self) -> None:
        self.spent = UsageCollector()
        self.asked: list[str] = []

    async def batch_metrics(self, hosts: Sequence[str], select: Sequence[str]) -> Response:
        self.asked.extend(hosts)
        cost = UnitsCost(actual=50, estimated=50, per_row=2)
        self.spent("batch_metrics", cost)
        rows = [
            {"url": f"https://{h}/", "domain_rating": KNOWN_DR[h]} for h in hosts if h in KNOWN_DR
        ]
        return Response(rows=rows, cost=cost)


async def _plenty() -> int:
    """Кап с запасом: здесь не о капе (`test_crawl_dr_cap.py`)."""
    return 1_000_000


def _link(donor: str, root: str, article: int) -> OutLink:
    anchor, sponsored, nofollow = SHAPES[root]
    return OutLink(
        page_url=f"https://{donor}/post/{article}",
        url=f"https://{root}/offer",
        target_host=root,
        target_root=root,
        anchor=anchor,
        anchor_key=anchor.lower(),
        nofollow=nofollow,
        sponsored=sponsored,
        ugc=False,
    )


async def _crawl(
    session: AsyncSession,
    provider: _Ahrefs,
    roots: list[str],
    *,
    article: int,
    donor: str = DONOR,
) -> None:
    """Обход донора, нашедший `roots` на статье `article`, и пересчёт — как задача после
    обхода: DR у провайдера, расход — в журнал той же сессией."""
    report = CrawlReport(
        host=donor,
        outcome=CrawlOutcome.OK,
        stop_reason=StopReason.EXHAUSTED,
        pages=[f"https://{donor}/post/{article}"],
        links=[_link(donor, root, article) for root in roots],
        articles=1,
    )
    run = await save_crawl(session, report)
    await session.flush()
    ratings = functools.partial(ahrefs_ratings, provider, allowance=_plenty)
    await gate.judge_run(session, run.id, ratings=ratings)  # type: ignore[arg-type]
    journal(session, provider.spent)
    await session.flush()


async def _candidates(session: AsyncSession) -> dict[tuple[str, str], CandidateModel]:
    rows = await session.scalars(select(CandidateModel))
    return {(row.donor_host, row.target_root): row for row in rows.all()}


async def _candidate(session: AsyncSession, root: str, donor: str = DONOR) -> CandidateModel:
    return (await _candidates(session))[donor, root]


async def _memory(session: AsyncSession) -> dict[str, CandidateMemoryModel]:
    """Память донора — как в базе: пересчёт пишет её мимо сессии."""
    rows = await session.scalars(
        select(CandidateMemoryModel)
        .where(CandidateMemoryModel.donor_host == DONOR)
        .execution_options(populate_existing=True)
    )
    return {row.target_root: row for row in rows.all()}


async def _advertiser(session: AsyncSession, host: str) -> AdvertiserModel | None:
    return await session.scalar(
        select(AdvertiserModel)
        .join(DomainModel, DomainModel.id == AdvertiserModel.domain_id)
        .where(DomainModel.host == host)
    )


async def _address(session: AsyncSession, host: str) -> None:
    """Адрес рекламодателю — как после поиска адресов: письмо есть куда собрать."""
    domain = await session.scalar(select(DomainModel).where(DomainModel.host == host))
    assert domain is not None
    session.add(ContactModel(domain_id=domain.id, email=f"ads@{host}", source=ContactSource.PAGE))
    await session.flush()


async def _journal(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(UsageRecordModel)) or 0)


class TestTheDecisionComesBack:
    async def test_not_writing_holds_after_a_recrawl_that_missed_the_domain(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """«Не пишем» → повтор без домена → повтор с ним на новой статье: решение на месте,
        рекламодатель не заводится заново, письмо не собирается, проверка оффера держит."""
        provider = _Ahrefs()
        await priced_donor(session, DONOR)
        await _crawl(session, provider, [BRAND, SOURCE], article=1)
        await promote(session)
        await _address(session, BRAND)
        decided = await review.decide(
            session, (await _candidate(session, BRAND)).id, confirmed=False, by=BY
        )
        when = decided.candidate.decided_at
        assert decided.withdrawn.removed

        await _crawl(session, provider, [SOURCE], article=2)
        await _crawl(session, provider, [BRAND, SOURCE], article=3)

        back = await _candidate(session, BRAND)
        assert back.best_page_url == f"https://{DONOR}/post/3"
        assert (back.verdict, back.confirmed, back.decided_by, back.decided_at) == (
            Verdict.BOUGHT,
            False,
            BY,
            when,
        )
        report = await promote(session)
        assert (report.considered, report.promoted) == (0, 0)
        assert await _advertiser(session, BRAND) is None
        built = await build_offers(session)
        assert built.prepared == 0  # type: ignore[attr-defined]
        assert await offers(session) == []
        # Проверки офферов видят то же, что сразу после решения: у домена одни снятые.
        assert await _only_rejected(session, BRAND)
        assert await _rejected_link(session, BRAND, back.best_page_url)

    async def test_writing_holds_too_and_the_human_is_not_asked_again(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """«Пишем» спорному → повтор без домена → повтор с ним: решение на месте — спорный
        не встаёт в очередь человека снова, перевод считает его решённым человеком,
        письмо собирается под ссылку новой статьи."""
        provider = _Ahrefs()
        await priced_donor(session, DONOR)
        await _crawl(session, provider, [MAYBE, SOURCE], article=1)
        maybe = await _candidate(session, MAYBE)
        assert maybe.verdict is Verdict.PENDING
        await review.decide(session, maybe.id, confirmed=True, by=BY)
        await promote(session)
        await _address(session, MAYBE)

        await _crawl(session, provider, [SOURCE], article=2)
        await _crawl(session, provider, [MAYBE, SOURCE], article=3)

        back = await _candidate(session, MAYBE)
        assert (back.verdict, back.confirmed, back.decided_by) == (Verdict.PENDING, True, BY)
        assert await review.queue(session) == []
        report = await promote(session)
        assert (report.considered, report.updated, report.by_human) == (1, 1, 1)
        advertiser = await _advertiser(session, MAYBE)
        assert advertiser is not None
        assert advertiser.confirmed_by_human is True
        assert advertiser.best_page_url == f"https://{DONOR}/post/3"
        built = await build_offers(session)
        assert built.prepared == 1  # type: ignore[attr-defined]

    async def test_a_decision_changed_after_the_return_beats_the_memory(
        self, session: AsyncSession
    ) -> None:
        """Передумали после возврата (`force`): память помнит прежнее, но строка кандидата
        сильнее — пересчёт с доменом её не перетирает, а следующий повтор без домена
        и с ним возвращает новое решение."""
        provider = _Ahrefs()
        second = "второй@team.example.test"
        await _crawl(session, provider, [BRAND, SOURCE], article=1)
        await review.decide(session, (await _candidate(session, BRAND)).id, confirmed=False, by=BY)
        await _crawl(session, provider, [SOURCE], article=2)
        await _crawl(session, provider, [BRAND, SOURCE], article=3)
        assert (await _memory(session))[BRAND].confirmed is False

        changed = await _candidate(session, BRAND)
        await review.decide(session, changed.id, confirmed=True, by=second, force=True)
        await _crawl(session, provider, [BRAND, SOURCE], article=4)
        assert (await _candidate(session, BRAND)).confirmed is True
        await _crawl(session, provider, [SOURCE], article=5)
        await _crawl(session, provider, [BRAND, SOURCE], article=6)

        back = await _candidate(session, BRAND)
        assert (back.confirmed, back.decided_by) == (True, second)
        assert (await _memory(session))[BRAND].confirmed is True

    async def test_the_memory_is_per_donor(self, session: AsyncSession) -> None:
        """Решение — про ссылку донора (`promote.py`): «не пишем» по ссылке одного донора
        не становится решением о ссылке второго, когда тот найдёт тот же домен."""
        provider = _Ahrefs()
        await _crawl(session, provider, [BRAND, SOURCE], article=1)
        await review.decide(session, (await _candidate(session, BRAND)).id, confirmed=False, by=BY)
        await _crawl(session, provider, [SOURCE], article=2)

        await _crawl(session, provider, [BRAND], article=1, donor=OTHER_DONOR)

        other = await _candidate(session, BRAND, donor=OTHER_DONOR)
        assert (other.verdict, other.confirmed) == (Verdict.BOUGHT, None)
        assert (await _memory(session))[BRAND].confirmed is False


class TestTheRatingIsNotBoughtAgain:
    async def test_a_returning_domain_is_not_asked_and_the_journal_stays(
        self, session: AsyncSession
    ) -> None:
        """DR вернувшегося домена — из памяти: провайдера о нём не спрашивают, журнал
        расхода не растёт, а у кандидата — тот DR и то время, когда за него заплатили."""
        provider = _Ahrefs()
        await _crawl(session, provider, [BRAND, MAYBE, SOURCE], article=1)
        assert sorted(provider.asked) == [BRAND, MAYBE]
        rated_at = (await _candidate(session, BRAND)).dr_checked_at
        assert rated_at is not None
        spent = await _journal(session)
        assert spent == 1

        await _crawl(session, provider, [SOURCE], article=2)
        await _crawl(session, provider, [BRAND, MAYBE, SOURCE], article=3)

        assert sorted(provider.asked) == [BRAND, MAYBE], "вернувшихся не спрашивают снова"
        assert await _journal(session) == spent
        back = await _candidate(session, BRAND)
        assert (back.dr, back.dr_checked_at) == (23, rated_at)
        assert (await _candidate(session, MAYBE)).dr == 31


class TestTheMemoryIsNotACandidate:
    async def test_a_recrawl_without_the_domain_leaves_it_out_of_every_list(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Повтор без домена: кандидата нет — ни в «куплено», ни в спорных, ни в переводе,
        ни у проверки оффера; решения и DR ждут в памяти донора. Память пишется пачками —
        здесь по одной строке, и ни одна не теряется."""
        monkeypatch.setattr(gate, "MEMORY_CHUNK", 1)
        provider = _Ahrefs()
        await _crawl(session, provider, [BRAND, MAYBE, SOURCE], article=1)
        await review.decide(session, (await _candidate(session, BRAND)).id, confirmed=False, by=BY)
        await review.decide(session, (await _candidate(session, MAYBE)).id, confirmed=True, by=BY)

        await _crawl(session, provider, [SOURCE], article=2)

        assert set(await _candidates(session)) == {(DONOR, SOURCE)}
        assert await sample.queue(session, verdict=Verdict.BOUGHT, include_decided=True) == []
        assert await review.queue(session, include_decided=True) == []
        assert (await standing(session)).ready == 0
        # Рекламодателя «не пишем» сняло само решение, и проверке оффера здесь держать нечего.
        assert not await _only_rejected(session, BRAND)
        memory = await _memory(session)
        assert {root: row.confirmed for root, row in memory.items()} == {BRAND: False, MAYBE: True}
        assert (memory[BRAND].dr, memory[MAYBE].dr) == (23, 31)


def _down_and_up(connection: Connection) -> tuple[bool, bool, set[str]]:
    """Вниз и вверх на соединении теста: подъём сьюта идёт подпроцессом, и покрытие его
    не видит. Откат снимает таблицу, подъём ставит её с ключом пары."""

    def present() -> bool:
        return bool(connection.scalar(text("SELECT to_regclass('candidate_memory') IS NOT NULL")))

    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = present()
        migration.upgrade()
    keys = connection.scalars(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = to_regclass('candidate_memory') AND contype = 'u'"
        )
    )
    return down, present(), set(keys)


async def test_the_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    down, up, keys = await connection.run_sync(_down_and_up)

    assert (down, up, keys) == (False, True, {"uq_candidate_memory_pair"})
