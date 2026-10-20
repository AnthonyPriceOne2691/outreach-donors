"""Досуд тематики: план до траты, каждый ответ — с записью, повтор берёт оставшихся.

Модель и главные — подставные: без сети. Проверяется то, что целиком наше: кого план
берёт и в каком порядке, откуда текст (у кого один заголовок главной — открыть живую), что
смета называет до траты, что каждый ответ записан вместе со строкой журнала, а прерванный
досуд — и «заново» до момента — продолжает с места, не платя второй раз.

Домены — в зоне `.example`: настоящим сайтом она быть не может, а `.test` досуд пропускает
сам — это демо-данные (`RESERVED_SUFFIX`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.features.core.domain import DonorStatus, RunStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.run import RunModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.donors.home_signals import HomeSignals
from backend.features.donors.verdict import Thresholds
from backend.features.runs.repository import RunRepository
from backend.features.topics import backfill as topic_backfill
from backend.features.topics.backfill import backfill_topics, plan_topics
from backend.features.topics.classifier import TOPIC_VERSION, SiteText, TopicVerdict
from backend.features.topics.cost import ESTIMATE
from backend.features.topics.store import TopicRecord, TopicStore
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tests.conftest import TEST_DSN

T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)
KNOWN = TopicRecord("travel", None, "путеводитель", "gpt-5-mini", TOPIC_VERSION, "serp")


async def _donor(
    session: AsyncSession, host: str, *, dr: int | None = 30, accepted: bool = False, **domain: Any
) -> None:
    row = DomainModel(host=host, **domain)
    session.add(row)
    await session.flush()
    review = "accepted" if accepted else None
    session.add(DonorModel(domain_id=row.id, status=DonorStatus.SUITABLE, dr=dr, review=review))
    await session.flush()


async def _saved_run(session: AsyncSession, texts: dict[str, dict[str, str]]) -> None:
    """Прогон с сохранённой выдачей — как её пишет прогон (`Candidates.as_dict`)."""
    settings = await RunRepository(session).create_settings(
        T, geo_top_n=5, geo_min_share=0.2, metrics_ttl_days=90, price_ttl_days=150,
        units_cap=100_000,
    )  # fmt: skip
    session.add(
        RunModel(
            stage=Stage.DONORS,
            settings_id=settings.id,
            status=RunStatus.DONE,
            keywords=["k"],
            country="us",
            candidates={"hosts": list(texts), "texts": texts},
        )
    )
    await session.flush()


async def _base(session: AsyncSession) -> None:
    """Пять доноров: принятый с выдачей, кандидат с заголовком главной из суда, кандидат
    без текста, уже с темой и выдуманный. DR — чтобы порядок был виден."""
    await _donor(session, "saved-page.example", dr=20, accepted=True)
    await _donor(
        session,
        "judged-home.example",
        dr=70,
        judge_home={"reached": True, "title": "Garden Weekly"},
    )
    await _donor(session, "no-text.example", dr=50)
    await _donor(session, "known.example", dr=90)
    await _donor(session, "demo.example.test", dr=99)
    await TopicStore(session).save(
        {"known.example": KNOWN}, now=datetime.now(UTC) - timedelta(days=1)
    )
    await _saved_run(
        session,
        {"saved-page.example": {"url": "https://saved-page.example/a", "title": "Loan rates"}},
    )


async def _spent(session: AsyncSession) -> int | None:
    """Токены тематики в журнале — суммой: строка на каждый ответ модели."""
    return await session.scalar(
        select(func.sum(UsageRecordModel.units)).where(UsageRecordModel.operation == "site_topic")
    )


class TestPlan:
    async def test_accepted_first_then_by_dr_and_text_by_the_ladder(
        self, session: AsyncSession
    ) -> None:
        await _base(session)

        plan = await plan_topics(session)

        assert plan.hosts == ["saved-page.example", "judged-home.example", "no-text.example"]
        assert (plan.accepted, plan.fresh, plan.skipped_reserved) == (1, 1, 1)
        assert plan.texts == {
            "saved-page.example": SiteText("Страница из выдачи: Loan rates", "serp")
        }
        assert plan.to_fetch == ["judged-home.example", "no-text.example"]
        assert plan.by_source == {"serp": 1}
        assert plan.per_site is ESTIMATE, "прогонов с темами нет — оценка, и так и сказано"
        assert plan.asked == 3
        assert plan.tokens == 3 * ESTIMATE.tokens
        assert plan.usd == pytest.approx(ESTIMATE.usd(3))

    async def test_a_lone_home_title_opens_the_live_home_and_stays_in_reserve(
        self, session: AsyncSession
    ) -> None:
        """По одному заголовку модель чаще говорит «не понять», а это знание на месяц: главную
        открываем (бесплатно), а заголовок из суда — запасом, если она не откроется."""
        await _base(session)

        plan = await plan_topics(session)

        assert "judged-home.example" in plan.to_fetch
        assert plan.fallback == {"judged-home.example": SiteText("Главная: Garden Weekly", "home")}

    async def test_without_home_the_title_is_the_text_and_the_textless_stay_without_a_topic(
        self, session: AsyncSession
    ) -> None:
        await _base(session)

        plan = await plan_topics(session, fetch_home=False)

        assert (plan.to_fetch, plan.fallback) == ([], {})
        assert plan.texts["judged-home.example"] == SiteText("Главная: Garden Weekly", "home")
        assert plan.by_source == {"serp": 1, "home": 1}
        assert (plan.asked, plan.without_text) == (2, 1)

    async def test_again_takes_the_fresh_written_before_the_moment_and_limit_cuts_the_order(
        self, session: AsyncSession
    ) -> None:
        await _base(session)

        again = await plan_topics(session, again_before=datetime.now(UTC))

        assert again.hosts[:2] == ["saved-page.example", "known.example"]
        assert again.fresh == 0
        assert (await plan_topics(session, limit=1)).hosts == ["saved-page.example"]

    async def test_again_before_the_same_moment_resumes_instead_of_paying_twice(
        self, session: AsyncSession
    ) -> None:
        """Первая попытка «заново» оборвалась, успев определить один сайт: повтор с тем же
        моментом его не берёт — тема записана позже момента."""
        await _base(session)
        moment = datetime.now(UTC)
        assert "known.example" in (await plan_topics(session, again_before=moment)).hosts

        await TopicStore(session).save({"known.example": KNOWN}, now=moment + timedelta(seconds=5))
        resumed = await plan_topics(session, again_before=moment)

        assert "known.example" not in resumed.hosts
        assert resumed.fresh == 1


def _model(monkeypatch: pytest.MonkeyPatch, answers: dict[str, TopicVerdict]) -> list[str]:
    asked: list[str] = []

    async def fake(http: httpx.AsyncClient, *, host: str, text: SiteText) -> TopicVerdict:
        asked.append(host)
        return answers[host]

    monkeypatch.setattr("backend.features.topics.step.classify", fake)
    return asked


def _topic(code: str | None, tokens: int = 1_000) -> TopicVerdict:
    return TopicVerdict(code, None, "по тексту", "gpt-5-mini", tokens, 800, tokens - 800, "serp")


async def _opened(client: httpx.AsyncClient, host: str) -> HomeSignals:
    return HomeSignals(reached=True, title=f"Home of {host}", description="Recipes and cooking")


class TestBackfill:
    async def test_topics_land_with_their_journal_line(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _base(session)
        monkeypatch.setattr(topic_backfill, "check_home", _opened)
        silent = TopicVerdict(
            None, None, "модель, сеть (можно повторить): таймаут", unanswered=True
        )
        _model(
            monkeypatch,
            {
                "saved-page.example": _topic("finance"),
                "judged-home.example": _topic("home"),
                "no-text.example": silent,
            },
        )
        plan = await plan_topics(session)

        report = await backfill_topics(session, plan, http=None, home_client=object())  # type: ignore[arg-type]

        topics = dict(
            (await session.execute(select(DomainModel.host, DomainModel.site_topic))).tuples().all()
        )
        assert topics["saved-page.example"] == "finance"
        assert topics["judged-home.example"] == "home"
        assert topics["no-text.example"] is None, "модель не ответила — на домен не легло"
        assert (report.summary.classified, report.summary.unanswered) == (2, 1)
        assert (report.fetched, report.by_title) == (2, 0), "обе главные открылись"
        assert "таймаут" in (report.summary.unanswered_reason or "")
        lines = await session.scalar(
            select(func.count()).where(UsageRecordModel.operation == "site_topic")
        )
        assert (await _spent(session), lines) == (2_000, 2), "строка журнала на каждый ответ"

    async def test_an_answer_out_of_form_keeps_the_known_topic(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """«Заново» по сайту с темой, а модель ответила прозой: прежняя тема цела, токены —
        в журнале, сбой — своим числом."""
        await _base(session)
        broken = TopicVerdict(
            None, None, "ответ модели не разобран: не JSON", "gpt-5-mini", 900, 700, 200,
            "serp", unanswered=True, unreadable=True,
        )  # fmt: skip
        _model(monkeypatch, {"known.example": broken})
        plan = await plan_topics(session, again_before=datetime.now(UTC), fetch_home=False)
        plan.hosts, plan.texts = (
            ["known.example"],
            {"known.example": SiteText("Главная: x", "home")},
        )

        report = await backfill_topics(session, plan, http=None, home_client=None)  # type: ignore[arg-type]

        domain = (
            await session.execute(select(DomainModel).where(DomainModel.host == "known.example"))
        ).scalar_one()
        assert domain.site_topic == "travel"
        assert (report.summary.unreadable, report.summary.unclear) == (1, 0)
        assert await _spent(session) == 900

    async def test_an_unopened_home_falls_back_to_the_judges_title(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _donor(session, "judged-home.example", judge_home={"reached": True, "title": "Dacha"})

        async def closed(client: httpx.AsyncClient, host: str) -> HomeSignals:
            return HomeSignals(reached=False, error="закрылась")

        monkeypatch.setattr(topic_backfill, "check_home", closed)
        read: list[SiteText] = []

        async def fake(http: httpx.AsyncClient, *, host: str, text: SiteText) -> TopicVerdict:
            read.append(text)
            return _topic("home")

        monkeypatch.setattr("backend.features.topics.step.classify", fake)

        report = await backfill_topics(
            session,
            await plan_topics(session),
            http=None,
            home_client=object(),  # type: ignore[arg-type]
        )

        assert read == [SiteText("Главная: Dacha", "home")]
        assert (report.unreached, report.by_title, report.summary.classified) == (1, 1, 1)

    async def test_a_closed_home_is_counted_not_asked(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _donor(session, "closed.example")

        async def closed(client: httpx.AsyncClient, host: str) -> HomeSignals:
            return HomeSignals(reached=False, error="закрылась")

        monkeypatch.setattr(topic_backfill, "check_home", closed)
        asked = _model(monkeypatch, {})

        report = await backfill_topics(
            session,
            await plan_topics(session),
            http=None,
            home_client=object(),  # type: ignore[arg-type]
        )

        assert asked == []
        assert (report.unreached, report.summary.asked) == (1, 0)

    async def test_without_home_client_nothing_is_fetched(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _donor(session, "no-text.example")
        asked = _model(monkeypatch, {})

        report = await backfill_topics(
            session,
            await plan_topics(session),
            http=None,
            home_client=None,  # type: ignore[arg-type]
        )

        assert asked == []
        assert report.no_text == 1


@pytest.fixture
async def committed() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Сессии с настоящими фиксациями: обрыв проверяется тем, что осталось в базе.
    После теста база вычищается целиком — остальные тесты ждут пустые таблицы."""
    engine = create_async_engine(TEST_DSN)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as conn:
            tables = (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                    )
                )
            ).scalars()
            listed = ", ".join(f'"{table}"' for table in tables)
            await conn.execute(text(f"TRUNCATE {listed} RESTART IDENTITY CASCADE"))
        await engine.dispose()


async def test_a_stop_keeps_the_answers_done_and_a_repeat_takes_the_rest(
    committed: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Потолок модели на второй пачке: ответы первой записаны вместе со строками журнала,
    повтор команды берёт только оставшихся."""
    async with committed() as session:
        for n in range(3):
            await _donor(
                session, f"site{n}.example", judge_home={"reached": True, "title": f"Site {n}"}
            )
        await session.commit()
    monkeypatch.setattr(topic_backfill, "CHUNK", 2)
    checks = {"n": 0}

    async def cap(session: AsyncSession, **kwargs: Any) -> None:
        checks["n"] += 1
        if checks["n"] == 2:
            raise LlmCapExceededError("потолок расхода на модель за день достигнут")

    monkeypatch.setattr(topic_backfill, "ensure_llm_within_cap", cap)
    _model(monkeypatch, {f"site{n}.example": _topic("news") for n in range(3)})

    async with committed() as session:
        with pytest.raises(LlmCapExceededError):
            await backfill_topics(session, await plan_topics(session), http=None, home_client=None)  # type: ignore[arg-type]

    async with committed() as session:
        assert (await plan_topics(session)).hosts == ["site2.example"], "повтор — оставшийся"
        assert await _spent(session) == 2_000, "ответы до остановки — в журнале"


async def test_a_failed_write_keeps_the_answers_written_before_it(
    committed: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Запись второго ответа упала посреди пачки: первый уже зафиксирован — темой и строкой
    журнала, — и проход падает громко, а не молча."""
    async with committed() as session:
        for n in range(2):
            await _donor(
                session, f"site{n}.example", judge_home={"reached": True, "title": f"Site {n}"}
            )
        await session.commit()
    _model(monkeypatch, {f"site{n}.example": _topic("news") for n in range(2)})
    saved = TopicStore.save
    calls = {"n": 0}

    async def flaky(self: TopicStore, records: Any, **kwargs: Any) -> int:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("связь с базой оборвалась")
        return await saved(self, records, **kwargs)

    monkeypatch.setattr(TopicStore, "save", flaky)
    monkeypatch.setattr(topic_backfill.cfg, "CONCURRENCY", 1)

    async with committed() as session:
        with pytest.raises(RuntimeError, match="связь с базой"):
            await backfill_topics(session, await plan_topics(session), http=None, home_client=None)  # type: ignore[arg-type]

    async with committed() as session:
        assert (await plan_topics(session)).hosts == ["site1.example"]
        assert await _spent(session) == 1_000
