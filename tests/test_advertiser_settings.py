"""Пороги Этапа 2 — настройками, а не кодом (ТЗ: «все пороги … вынесены в настройки»).

До сверки ТЗ 11.10.2026 веса скоринга, «DR > 80» и списки «кому не пишем» жили
константами в коде: поменять порог значило выпустить новую версию. Теперь они —
настройки окружения (`config/advertisers.py`), а экран «Пороги» показывает,
что действует. Умолчания — те же замеренные числа, что стояли в коде.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from backend.config.advertisers import _Advertisers
from backend.features.core.domain import (
    CrawlOutcome,
    CrawlStatus,
    StopReason,
    UserRole,
    Verdict,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.crawl import scoring, selection_settings
from backend.features.crawl.gate import judge_run
from backend.features.crawl.links import OutLink
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer


def _settings(**env: str) -> _Advertisers:
    """Настройки только из `env`: `.env` разработчика тест не читает."""
    return _Advertisers(_env_file=None, **env)  # type: ignore[call-arg]


class TestDefaults:
    def test_the_requirement_scale_is_the_default(self) -> None:
        """Шкала требования: sponsored +5, пометка +4, dofollow из тела +2,
        коммерческий анкор +1, тематика +1; ≥ 4 — куплена, 2–3 — на проверку."""
        found = _settings()

        assert (
            found.points_rel_sponsored,
            found.points_marker,
            found.points_dofollow_in_body,
            found.points_commercial_anchor,
            found.points_off_topic,
        ) == (5, 4, 2, 1, 1)
        assert (found.bought_at, found.review_at, found.max_dr) == (4, 2, 80)

    def test_measured_weights_and_lists_are_kept(self) -> None:
        found = _settings()

        assert (found.points_repeated, found.repeat_pages, found.points_paid_looking) == (3, 3, 2)
        assert "wikipedia.org" in found.deny_reference
        assert ".pdf" in found.deny_documents
        assert found.nonprofit_zones == [".org"]


class TestTheEnvironmentDecides:
    def test_a_weight_and_a_list_come_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ADVERTISERS_BOUGHT_AT", "5")
        monkeypatch.setenv("ADVERTISERS_DENY_MEDIA", '[" News.Example.test "]')

        found = _settings()

        assert found.bought_at == 5
        assert found.deny_media == ["news.example.test"]

    def test_review_must_stay_below_bought(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ADVERTISERS_REVIEW_AT", "4")

        with pytest.raises(ValidationError, match="ADVERTISERS_REVIEW_AT"):
            _settings()

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("ADVERTISERS_DENY_SOCIAL", '["not a domain"]'),
            ("ADVERTISERS_DENY_DOCUMENTS", '["pdf"]'),
            ("ADVERTISERS_MAX_DR", "120"),
        ],
    )
    def test_a_bad_value_stops_the_start(
        self, monkeypatch: pytest.MonkeyPatch, name: str, value: str
    ) -> None:
        """Опечатка в списке — отказ при старте, а не молча пропущенная запись."""
        monkeypatch.setenv(name, value)

        with pytest.raises(ValidationError):
            _settings()


class TestScoringReadsTheSettings:
    def test_a_configured_nonprofit_zone_is_named_in_the_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Зона «куплено только человеком» — настройкой; причина называет ту, что совпала,
        а не «.org» всегда."""
        monkeypatch.setattr(scoring, "NONPROFIT_SUFFIXES", (".org", ".fund.example"))
        link = OutLink(
            page_url="https://donor.example.test/post/",
            url="https://help.fund.example/donate",
            target_host="help.fund.example",
            target_root="help.fund.example",
            anchor="Donate",
            anchor_key="donate",
            nofollow=False,
            sponsored=True,
            ugc=False,
        )

        (candidate,) = scoring.score_candidates([link], donor_root="donor.example.test")

        assert candidate.verdict is Verdict.PENDING
        assert any(reason.startswith("зона .fund.example") for reason in candidate.reasons)

    @pytest.mark.parametrize(("boost_donors", "boosted"), [(2, True), (3, False)])
    def test_the_across_donors_boost_is_a_setting(
        self, monkeypatch: pytest.MonkeyPatch, boost_donors: int, boosted: bool
    ) -> None:
        """Усилитель «у нескольких наших доноров» — настройкой: порог требования
        «≥ 2» по умолчанию, и его можно поднять."""
        monkeypatch.setattr(scoring, "BOOST_DONORS", boost_donors)
        candidate = scoring.Candidate(
            target_root="brand.example.test",
            points=3,
            verdict=Verdict.PENDING,
            strong=True,
        )

        scoring.boost_across_donors([candidate], {"brand.example.test": 2})

        assert (candidate.verdict is Verdict.BOUGHT) is boosted


class TestTheJudgedWithSnapshot:
    async def test_the_crawl_keeps_what_judged_it(self, session: AsyncSession) -> None:
        """Пересчёт кладёт на обход снимок действующих весов и порогов: веса меняются
        окружением, а вердикт должен оставаться объяснимым и через месяц."""
        run = CrawlRunModel(
            host="donor.example.test",
            status=CrawlStatus.DONE,
            outcome=CrawlOutcome.OK,
            stop_reason=StopReason.EXHAUSTED,
            pages_opened=1,
            articles=1,
            stats={"links_found": 0},
        )
        session.add(run)
        await session.flush()

        await judge_run(session, run.id)

        await session.refresh(run)
        assert run.stats is not None
        judged = run.stats["scored_with"]
        assert judged["ADVERTISERS_BOUGHT_AT"] == scoring.BOUGHT_AT
        assert judged["ADVERTISERS_BOOST_DONORS"] == scoring.BOOST_DONORS
        assert judged["CRAWL_STALE_YEARS"] >= 1
        assert "bbc.com" in judged["ADVERTISERS_DENY_MEDIA"]
        assert run.stats["links_found"] == 0, "отчёт обхода остаётся на месте"


class TestShownOnTheScreen:
    def test_the_screen_shows_what_scoring_uses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scoring, "BOUGHT_AT", 6)

        found = selection_settings.in_force()

        bought = next(item for item in found.thresholds if item.setting == "ADVERTISERS_BOUGHT_AT")
        assert bought.value == 6
        assert {item.setting for item in found.lists} >= {
            "ADVERTISERS_DENY_MEDIA",
            "ADVERTISERS_DENY_ZONES",
        }

    async def test_route_answers_any_viewer(
        self,
        client: AsyncClient,
        make_user: Callable[..., Awaitable[UserModel]],
        sign_in: Callable[..., Awaitable[str]],
    ) -> None:
        await make_user("viewer@site.example.test", role=UserRole.OPERATOR)
        token = await sign_in("viewer@site.example.test")

        response = await client.get("/api/settings/advertisers", headers=bearer(token))
        anonymous = await client.get("/api/settings/advertisers")

        assert response.status_code == 200, response.text
        body = response.json()
        dr = next(item for item in body["thresholds"] if item["setting"] == "ADVERTISERS_MAX_DR")
        assert dr["value"] == 80
        media = next(item for item in body["lists"] if item["setting"] == "ADVERTISERS_DENY_MEDIA")
        assert "bbc.com" in media["values"]
        assert anonymous.status_code == 403, "без пропуска — отказ"
