"""Кандидат без текста выдачи — судья читает главную; не открылась — вердикта нет и кэша нет.

Ревью 20.10.2026: кандидаты из списков ссылок конкурентов приходят без текста выдачи, и судья
ставил им «посмотри» правилом, не зовя модель, а кэш держал это полгода — прогон по ключам,
нашедший те же домены с текстом, их не судил. Здесь — общий путь любого входа: главная тем же
походом, что вторая сторона судьи, по ней же — тема сайта; ни выдачи, ни главной — вердикта
нет, домен судится снова, а в отчёте прогона — числом.

Модель и сеть подменены: судья и тематика — записью того, что им дали прочесть, главные — по
словарю. Домены — RFC 2606: прогон сводит адрес к домену по списку суффиксов.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from backend.config import topics as topics_cfg
from backend.config.judge import JudgeMode
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.run import RunModel
from backend.features.donors import judging
from backend.features.donors.home_signals import HomeSignals
from backend.features.donors.publisher_judge import (
    NO_TEXT,
    Decider,
    Intent,
    Judgement,
    Recommendation,
    before_model,
)
from backend.features.donors.repository import DonorRepository
from backend.features.runs.competitor_run import prepare
from backend.features.runs.pipeline import RunRequest, execute_run
from backend.features.runs.planning import SerpText
from backend.features.serp.protocol import SerpResult
from backend.features.topics.classifier import SiteText, TopicVerdict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_execute_run as keyword_run
from tests.test_competitor_run import Ahrefs, T, _rows, _run
from tests.test_competitor_run_money import _deps, _request

#: Главная, по которой судят кандидата без текста выдачи.
GARDEN = HomeSignals(reached=True, title="Garden Daily", description="Notes on gardening")
#: Списки конкурентов: по сайту на каждого.
LISTS = {"example.com": _rows("example.org"), "example.net": _rows("example.co.uk")}
PLACEMENT = Judgement(Intent.SELLS_PLACEMENT, Recommendation.ACCEPT, None, "write for us", "m", 900)


class Read:
    """Что прочли судья, тематика и кто ходил за главными."""

    def __init__(self) -> None:
        self.judge: list[tuple[str, str | None, str | None]] = []
        self.homes: list[str] = []
        self.topics: dict[str, SiteText] = {}
        #: Главные, которые откроются; прочие — «не ответила».
        self.homes_of: dict[str, HomeSignals] = {}


@pytest.fixture
def read(monkeypatch: pytest.MonkeyPatch) -> Read:
    """Судья и тематика включены и подменены; главные — `HOMES` теста, прочие не открылись."""
    seen = Read()

    async def judge(_http: object, *, host: str, title: str | None, **kw: Any) -> Judgement:
        description = kw.get("description")
        seen.judge.append((host, title, description))
        ruled = before_model(host, " ".join(part for part in (title, description) if part))
        return ruled if ruled is not None else PLACEMENT

    async def home(_client: object, host: str) -> HomeSignals:
        seen.homes.append(host)
        return seen.homes_of.get(host, HomeSignals(reached=False, error="не ответила"))

    async def topic(_http: object, *, host: str, text: SiteText) -> TopicVerdict:
        seen.topics[host] = text
        return TopicVerdict("business", None, "по главной", "m", 300, 200, 100, text.source)

    monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.SHADOW)
    monkeypatch.setattr("backend.config.judge.HOME_CHECK", True)
    monkeypatch.setattr(topics_cfg, "MODE", topics_cfg.TopicMode.ON)
    monkeypatch.setattr(judging, "judge_host", judge)
    monkeypatch.setattr(judging, "check_home", home)
    monkeypatch.setattr("backend.features.topics.step.classify", topic)
    monkeypatch.setattr("backend.features.ahrefs.client.asyncio.sleep", _instant)
    return seen


async def _instant(_seconds: float) -> None:
    return None


async def _competitor_run(session: AsyncSession) -> tuple[RunModel, Any]:
    run = await _run(session)
    client = Ahrefs(LISTS).client()
    prepared = await prepare(session, client, run, T, cap=50_000)
    return run, await execute_run(_deps(session, client), _request(run, prepared))


async def _verdict(session: AsyncSession, host: str) -> DomainModel | None:
    return await session.scalar(select(DomainModel).where(DomainModel.host == host))


class TestCompetitorRun:
    async def test_judge_and_topic_read_the_home_of_a_site_without_serp_text(
        self, session: AsyncSession, read: Read
    ) -> None:
        read.homes_of.update({"example.org": GARDEN, "example.co.uk": GARDEN})

        run, report = await _competitor_run(session)

        assert sorted(read.homes) == ["example.co.uk", "example.org"]
        assert ("example.org", "Garden Daily", "Notes on gardening") in read.judge
        assert "Garden Daily" in read.topics["example.org"].text
        assert read.topics["example.org"].source == "home"
        stored = await _verdict(session, "example.org")
        assert stored is not None
        assert stored.judge_reason is not None
        assert stored.judge_reason.startswith(judging.BY_HOME)
        assert stored.site_topic == "business"
        assert report.judge is not None
        assert (report.judge.judged, report.judge.no_text) == (2, 0)
        assert (run.stats or {})["judge"]["no_text"] == 0

    async def test_closed_home_leaves_no_verdict_and_the_next_keyword_run_judges_again(
        self, session: AsyncSession, read: Read
    ) -> None:
        """Главная не открылась: вердикта нет, темы нет, в отчёте — числом. Прогон по ключам,
        нашедший тот же домен с текстом выдачи, судит его сразу, а не через полгода."""
        run, report = await _competitor_run(session)

        assert report.judge is not None
        assert (report.judge.judged, report.judge.no_text) == (0, 2)
        assert (run.stats or {})["judge"]["no_text"] == 2
        stored = await _verdict(session, "example.org")
        assert stored is None or stored.judged_at is None, "вердикта по пустоте нет"
        assert read.topics == {}, "тема не пишется: читать было нечего"
        assert await DonorRepository(session).fresh_judged(["example.org"]) == {}

        read.judge.clear()
        serp = Titled(["https://example.org/garden-tips"])
        hosts = keyword_run._ahrefs({"example.org": keyword_run.GOOD})
        await execute_run(
            await keyword_run._deps(session, serp, hosts),
            RunRequest(
                keywords=["garden tips"],
                country="us",
                thresholds=keyword_run.T,
                settings_id=await keyword_run._settings_id(session),
            ),
        )

        assert ("example.org", "Tips for gardeners", None) in read.judge


class Titled(keyword_run.FakeSerp):
    """Выдача с заголовком у каждой строки."""

    async def search(
        self, keywords: Any, country: str, *, depth_pages: int = 1
    ) -> dict[str, list[SerpResult]]:
        return {
            kw: [
                SerpResult(i + 1, url, title="Tips for gardeners")
                for i, url in enumerate(self._urls)
            ]
            for kw in keywords
        }


class TestPass:
    """Проход судьи без базы: что решает главная, когда текста выдачи нет."""

    async def _one(self, host: str, text: SerpText | None, read: Read) -> judging.JudgePass:
        return await judging.judge_candidates(
            object(),  # type: ignore[arg-type]
            [host],
            {host: text} if text else {},
            home_client=object(),  # type: ignore[arg-type]
        )

    async def test_serp_text_is_judged_as_before_without_the_home(self, read: Read) -> None:
        text = SerpText(
            url="https://example.org/a", title="Write for us", description="Guest posts"
        )

        passed = await self._one("example.org", text, read)

        assert read.judge == [("example.org", "Write for us", "Guest posts")]
        assert read.homes == [], "площадку по выдаче вторая сторона не проверяет"
        assert passed.verdicts["example.org"].reason == "write for us"

    async def test_home_without_any_text_is_no_verdict(self, read: Read) -> None:
        read.homes_of["example.org"] = HomeSignals(reached=True)

        passed = await self._one("example.org", None, read)

        assert passed.verdicts == {}
        assert passed.summary.no_text == 1
        assert passed.summary.judged == 0

    async def test_platform_needs_no_text_and_stays_a_rule(self, read: Read) -> None:
        passed = await self._one("someone.github.io", None, read)

        assert read.homes == [], "платформу решает правило, главная не нужна"
        assert passed.verdicts["someone.github.io"].decided_by == Decider.RULE.value

    async def test_without_a_home_client_there_is_no_verdict_either(self, read: Read) -> None:
        passed = await judging.judge_candidates(
            object(),  # type: ignore[arg-type]
            ["example.org"],
            {},
        )

        assert passed.verdicts == {}
        assert passed.summary.no_text == 1

    @pytest.mark.parametrize(
        ("home", "expected"),
        [
            (GARDEN, Recommendation.REVIEW),
            (HomeSignals(reached=True, title="Shop", shop=("cart",)), Recommendation.REJECT),
        ],
    )
    async def test_a_model_refusal_by_the_home_cuts_only_with_structure(
        self,
        read: Read,
        monkeypatch: pytest.MonkeyPatch,
        home: HomeSignals,
        expected: Recommendation,
    ) -> None:
        """Как у досуда по главной: режет только структура (корзина, услуга)."""
        read.homes_of["example.org"] = home

        async def sells(_http: object, **_: Any) -> Judgement:
            return Judgement(Intent.SELLS_OWN, Recommendation.REJECT, None, "магазин", "m", 900)

        monkeypatch.setattr(judging, "judge_host", sells)

        passed = await self._one("example.org", None, read)

        assert passed.verdicts["example.org"].recommendation == expected.value


async def test_a_rule_verdict_of_nothing_to_read_is_not_a_cache(session: AsyncSession) -> None:
    """Строка «судить было не по чему», записанная до 20.10.2026 правилом, — не кэш: судья
    берёт домен снова."""
    session.add_all(
        [
            DomainModel(host="blind.example.org", judge_decided_by="rule", judge_reason=NO_TEXT),
            DomainModel(host="platform.example.org", judge_decided_by="rule", judge_reason="x"),
        ]
    )
    await session.flush()
    await session.execute(DomainModel.__table__.update().values(judged_at=datetime.now(UTC)))

    fresh = await DonorRepository(session).fresh_judged(
        ["blind.example.org", "platform.example.org"]
    )

    assert set(fresh) == {"platform.example.org"}
