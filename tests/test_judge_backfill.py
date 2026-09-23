"""Досуд базы, собранной до судьи.

Проверяется то, что стоит денег или врёт молча: выдуманные домены не
судятся, текст берётся от лучшего источника к худшему, вердикт по главной
помечен, а решение человека досуд не трогает.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.features.core.domain import DonorStatus, RunStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.run import RunModel
from backend.features.donors import backfill as module
from backend.features.donors.backfill import FROM_HOME, backfill, plan_backfill
from backend.features.donors.home_signals import HomeSignals
from backend.features.donors.publisher_judge import Intent, Judgement, Recommendation
from backend.features.donors.verdict import Thresholds
from backend.features.runs.planning import Candidates, SerpText
from backend.features.runs.repository import RunRepository
from backend.features.serp.protocol import SerpResult


class SiteSearch:
    """Источник выдачи для `site:`: у homeonly.com в индексе одна заглавная."""

    spent = 0.0

    async def search(
        self, keywords: list[str], country: str, **_: object
    ) -> dict[str, list[SerpResult]]:
        self.spent += 0.001 * len(keywords)
        return {
            "site:homeonly.com": [SerpResult(1, "https://homeonly.com/", "Home")],
            "site:sitefound.com": [
                SerpResult(1, "https://sitefound.com/", "Shop now"),
                SerpResult(2, "https://sitefound.com/guide/x", "A guide to x"),
            ],
        }


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)


async def _donor(session: AsyncSession, host: str, **domain: Any) -> DomainModel:
    row = DomainModel(host=host, **domain)
    session.add(row)
    await session.flush()
    session.add(DonorModel(domain_id=row.id, status=DonorStatus.SUITABLE, dr=50))
    return row


async def _run(session: AsyncSession, candidates: dict[str, Any] | None, **extra: Any) -> None:
    settings = await RunRepository(session).create_settings(
        T, geo_top_n=5, geo_min_share=0.2, metrics_ttl_days=90, price_ttl_days=150,
        units_cap=100_000,
    )  # fmt: skip
    session.add(
        RunModel(
            stage=Stage.DONORS,
            settings_id=settings.id,
            status=RunStatus.DONE,
            keywords=extra.get("keywords", ["k"]),
            country="us",
            candidates=candidates,
        )
    )
    await session.flush()


@pytest.fixture
async def base(session: AsyncSession) -> None:
    await _donor(session, "saved.com")
    await _donor(session, "searched.com")
    await _donor(session, "homeonly.com")
    await _donor(session, "sitefound.com")
    await _donor(session, "demo.example.test")
    await _donor(session, "human.com", human_intent="publisher")
    # Старый прогон: текст не сохранялся, ключи есть — выдачу можно повторить.
    await _run(session, None, keywords=["old key"])
    await _run(
        session,
        {
            "hosts": ["saved.com"],
            "texts": {"saved.com": {"url": "https://saved.com/a", "title": "Old"}},
        },
    )
    await _run(
        session,
        {
            "hosts": ["saved.com"],
            "texts": {"saved.com": {"url": "https://saved.com/b", "title": "New"}},
        },
    )
    await session.commit()


async def test_plan_skips_fake_and_human_and_takes_freshest_text(
    session: AsyncSession, base: None
) -> None:
    plan = await plan_backfill(session)

    assert sorted(plan.hosts) == ["homeonly.com", "saved.com", "searched.com", "sitefound.com"]
    assert plan.skipped_reserved == 1, "зона .test — выдуманные домены, судить их нечего"
    assert plan.saved["saved.com"].url == "https://saved.com/b", "берётся свежий прогон"
    assert plan.searches == [("us", ["old key"], 1)]


async def test_ladder_saved_then_search_then_home(
    session: AsyncSession, base: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_gather(
        provider: object, keywords: list[str], country: str, **_: object
    ) -> Candidates:
        text = SerpText(url="https://searched.com/x", title="Review of things")
        return Candidates(hosts=["searched.com"], keywords=1, results=1, empty_keywords=[], dropped=0,
                          texts={"searched.com": text},
                          cost_usd=0.002)  # fmt: skip

    async def fake_home(client: object, host: str) -> HomeSignals:
        return HomeSignals(reached=True, title="Home page", description="about us")

    judged_on: dict[str, str] = {}

    async def fake_judge(http: object, **kwargs: object) -> Judgement:
        judged_on[str(kwargs["host"])] = str(kwargs["title"])
        if kwargs["host"] == "homeonly.com":
            # По главной модель путает издание со своим курсом с магазином.
            return Judgement(Intent.SELLS_OWN, Recommendation.REJECT, "x", "продаёт своё", "m", 100)
        return Judgement(Intent.EDITORIAL_ADS, Recommendation.ACCEPT, "x", "издание", "m", 100)

    async def fake_arbiter(http: object, **kwargs: object) -> Judgement:
        return Judgement(Intent.EDITORIAL_ADS, Recommendation.ACCEPT, "x", "издание", "m", 50)

    monkeypatch.setattr("backend.features.donors.judging.arbitrate", fake_arbiter)
    monkeypatch.setattr(module, "gather_candidates", fake_gather)
    monkeypatch.setattr(module, "check_home", fake_home)
    monkeypatch.setattr("backend.features.donors.judging.judge_host", fake_judge)
    monkeypatch.setattr("backend.features.donors.judging.check_home", fake_home)

    plan = await plan_backfill(session)
    report = await backfill(
        session,
        plan,
        http=None,
        home_client=object(),
        provider=SiteSearch(),  # type: ignore[arg-type]
    )
    await session.commit()

    # ⚠ Заглавная из `site:` пропущена: судья настроен на страницу по теме.
    assert judged_on == {"saved.com": "New", "searched.com": "Review of things",
                         "sitefound.com": "A guide to x", "homeonly.com": "Home page"}  # fmt: skip
    assert report.by_source == {
        "сохранённая выдача": 1, "выдача заново": 1, "поиск site:": 1, "главная": 1,
    }  # fmt: skip
    assert report.serp_cost_usd == pytest.approx(0.004)
    # Расход — в журнале, и по пачкам, а не в конце.
    spent = {
        r.operation: r for r in (await session.execute(select(UsageRecordModel))).scalars().all()
    }
    assert float(spent["serp_search"].amount_usd) == pytest.approx(0.004)
    assert spent["site_judge"].units == report.judge.tokens

    rows = {d.host: d for d in (await session.execute(select(DomainModel))).scalars().all()}
    assert rows["homeonly.com"].judge_reason.startswith(FROM_HOME), "вердикт по главной помечен"
    # По главной модель одна не режет — её отказ идёт человеку.
    assert rows["homeonly.com"].judge_recommendation == "review"
    assert not rows["saved.com"].judge_reason.startswith(FROM_HOME)
    # ⚠ Решение человека досуд не трогает, и выдуманный домен не судится.
    assert rows["human.com"].judged_at is None
    assert rows["demo.example.test"].judged_at is None


async def test_without_serp_goes_straight_to_home(
    session: AsyncSession, base: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_: object, **__: object) -> Candidates:
        raise AssertionError("--no-serp: за выдачу платить нельзя")

    async def closed(client: object, host: str) -> HomeSignals:
        return HomeSignals(reached=False, error="закрылась")

    async def fake_judge(http: object, **kwargs: object) -> Judgement:
        return Judgement(Intent.EDITORIAL_ADS, Recommendation.ACCEPT, "x", "издание", "m", 100)

    monkeypatch.setattr(module, "gather_candidates", boom)
    monkeypatch.setattr(module, "check_home", closed)
    monkeypatch.setattr("backend.features.donors.judging.judge_host", fake_judge)
    monkeypatch.setattr("backend.features.donors.judging.check_home", closed)

    plan = await plan_backfill(session)
    report = await backfill(
        session,
        plan,
        http=None,
        home_client=object(),
        provider=None,  # type: ignore[arg-type]
    )

    assert report.no_text == 3, "без текста не судим: вердикт по пустоте был бы угадан"
    assert report.judge.judged == 1
