"""«Тематика не совпадает» +1 — строка шкалы требования (сверка ТЗ 11.10.2026).

Тема донора — ключи прогона, по которым он нашёлся; тема ссылок — анкор и путь.
Половина проверок — когда правило МОЛЧИТ: незнание темы — не несовпадение, а лишний
балл дороже пропущенного. И балл слабый: без сильного признака рекламы «куплено»
из него не набирается — решает человек.
"""

from __future__ import annotations

from datetime import date

from backend.features.core.domain import (
    CrawlOutcome,
    RunStatus,
    Stage,
    StopReason,
    Verdict,
)
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.crawl.gate import judge_run
from backend.features.crawl.links import OutLink
from backend.features.crawl.scoring import Candidate, score_candidates
from backend.features.crawl.topic import donor_topic, off_topic
from backend.features.donors.verdict import Thresholds
from backend.features.runs.repository import RunRepository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

DONOR = "donor.example.test"
BETTING = ["best betting sites south africa", "free bets za"]


def _link(
    url: str, anchor: str, *, root: str | None = None, dofollow_in_body: bool = True
) -> OutLink:
    host = url.split("/")[2]
    return OutLink(
        page_url=f"https://{DONOR}/post/",
        url=url,
        target_host=host,
        target_root=root or host,
        anchor=anchor,
        anchor_key=anchor.lower(),
        nofollow=not dofollow_in_body,
        sponsored=False,
        ugc=False,
        in_body=True,
    )


class TestWhenTheRuleSpeaks:
    def test_a_loans_page_on_a_betting_donor(self) -> None:
        link = _link("https://lender.example.test/payday-loans/", "fast cash today")

        reason = off_topic([link], BETTING)

        assert reason is not None
        assert "payday" in reason
        assert "loans" in reason

    def test_the_same_theme_in_other_word_forms(self) -> None:
        """«bet» и «betting», «sports» и «sport» — одна тема."""
        link = _link("https://bookie.example.test/sports/bet/", "football odds")

        assert off_topic([link], BETTING) is None

    def test_one_link_on_topic_is_enough(self) -> None:
        links = [
            _link("https://brand.example.test/loans/", "car loans"),
            _link("https://brand.example.test/betting/", "live scores"),
        ]

        assert off_topic(links, BETTING) is None


class TestWhenItKeepsSilent:
    def test_without_the_donor_topic(self) -> None:
        """Донор заведён руками — прогона, а с ним и темы, у него нет."""
        link = _link("https://lender.example.test/payday-loans/", "fast cash")

        assert off_topic([link], []) is None

    def test_a_brand_name_says_nothing_about_the_theme(self) -> None:
        link = _link("https://acme.example.test/", "Acme")

        assert off_topic([link], BETTING) is None

    def test_calls_and_deal_words_are_about_the_action(self) -> None:
        links = [
            _link("https://bookie.example.test/promo", "Claim your bonus"),
            _link("https://bookie.example.test/", "Sign up now"),
            _link("https://bookie.example.test/review", "honest review and discount"),
        ]

        assert off_topic(links, BETTING) is None

    def test_a_single_word_is_not_a_theme(self) -> None:
        link = _link("https://acme.example.test/", "the honest one")

        assert off_topic([link], BETTING) is None

    def test_an_address_as_the_anchor_is_not_text(self) -> None:
        link = _link("https://acme.example.test/", "https://acme.example.test/")

        assert off_topic([link], BETTING) is None


def _one(links: list[OutLink], topic: list[str]) -> Candidate:
    (candidate,) = score_candidates(links, donor_root=DONOR, today=date(2026, 10, 11), topic=topic)
    return candidate


class TestScore:
    def test_a_strong_link_off_topic_reaches_bought(self) -> None:
        """Призыв в теле статьи, dofollow, тема чужая: 3 балла становятся 4."""
        link = _link("https://lender.example.test/payday-loans/", "Apply now for payday loans")

        without = _one([link], [])
        with_topic = _one([link], BETTING)

        assert (without.points, without.verdict) == (3, Verdict.PENDING)
        assert (with_topic.points, with_topic.verdict) == (4, Verdict.BOUGHT)
        assert any(r.startswith("тематика не совпадает") for r in with_topic.reasons)

    def test_weak_signals_and_the_theme_stay_for_a_human(self) -> None:
        """Товар серой ниши — слабый признак: с тематикой балл 4, но «куплено» из
        слабых не набирается — «спорно», и причина сказана."""
        link = _link("https://store.example.test/vape-kits/", "cheap vape kits")

        candidate = _one([link], BETTING)

        assert candidate.points == 4
        assert candidate.verdict is Verdict.PENDING
        assert any("решает человек" in reason for reason in candidate.reasons)

    def test_on_topic_changes_nothing(self) -> None:
        link = _link("https://bookie.example.test/betting/", "Apply now")

        assert _one([link], BETTING).points == _one([link], []).points


async def _settings_id(session: AsyncSession) -> int:
    settings = await RunRepository(session).create_settings(
        Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    return settings.id


async def _search_run(
    session: AsyncSession, keywords: list[str], found_by: dict[str, list[str]] | None
) -> RunModel:
    run = RunModel(
        stage=Stage.DONORS,
        settings_id=await _settings_id(session),
        status=RunStatus.DONE,
        keywords=keywords,
        country="za",
        candidates={"hosts": [DONOR], "found_by": found_by} if found_by is not None else None,
    )
    session.add(run)
    await session.flush()
    return run


class TestDonorTopic:
    async def test_keys_that_found_the_donor_then_the_whole_run(
        self, session: AsyncSession
    ) -> None:
        """Ключи, по которым донор нашёлся, — из выдачи; у прогона, где он стоит
        кандидатом без записи «нашёлся по», — все ключи прогона."""
        domain = DomainModel(host=DONOR)
        session.add(domain)
        await session.flush()
        await _search_run(session, ["casino", "free bets za"], {DONOR: ["free bets za"]})
        older = await _search_run(session, ["sports betting", "odds"], None)
        session.add(RunCandidateModel(run_id=older.id, domain_id=domain.id))
        await _search_run(session, ["crm"], {"other.example.test": ["crm"]})
        await session.flush()

        assert await donor_topic(session, DONOR) == ["free bets za", "sports betting", "odds"]
        assert await donor_topic(session, "manual.example.test") == []

    async def test_the_gate_scores_with_the_donor_topic(self, session: AsyncSession) -> None:
        await _search_run(session, BETTING, {DONOR: BETTING})
        crawl = CrawlRunModel(
            host=DONOR,
            outcome=CrawlOutcome.OK,
            stop_reason=StopReason.EXHAUSTED,
            pages_opened=1,
            articles=1,
        )
        session.add(crawl)
        await session.flush()
        session.add(
            OutLinkModel(
                crawl_run_id=crawl.id,
                page_url=f"https://{DONOR}/post/",
                url="https://lender.example.test/payday-loans/",
                target_host="lender.example.test",
                target_root="lender.example.test",
                anchor="Apply now for payday loans",
                anchor_key="apply now for payday loans",
                in_body=True,
            )
        )
        await session.flush()

        await judge_run(session, crawl.id)

        row = await session.scalar(select(CandidateModel))
        assert row is not None
        assert row.verdict is Verdict.BOUGHT
        assert any(str(reason).startswith("тематика не совпадает") for reason in row.reasons or [])
