"""«Кому не пишем» Этапа 2 распространяется на Этап 1 — по ТЗ.

ТЗ: «список „кому не пишем“ Этапа 2 распространяется на Этап 1» — wikipedia, gov, edu,
крупные СМИ, соцсети, DR > 80. До 11.10.2026 по имени до Ahrefs отсекались только зоны
и платформы, а DR > 80 и крупные СМИ уходили в конец очереди рассмотрения — полкой,
которую человек мог принять, и тогда им собирались письма. Решение владельца: по ТЗ.

Домены списков берутся из самих списков (`crawl/denylist.py`), а не пишутся здесь:
настоящих имён в публичном репозитории тестам незачем.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from backend.features.ahrefs.client import AhrefsClient
from backend.features.core.domain import ContactSource, DonorStatus, RunStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.crawl.denylist import MAJOR_MEDIA, REFERENCE_DOMAINS, SOCIAL_DOMAINS
from backend.features.donors.collect import DomainResult, collect
from backend.features.donors.publisher_judge import is_platform
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Thresholds, big_site_reason, check_dr
from backend.features.letters.recipients import Recipients
from backend.features.letters.repository import LetterRepository
from backend.features.review.candidates import RunReview
from backend.features.runs.exclusions import NOT_WRITING_WORDS, ExclusionReason, Exclusions, by_name
from backend.features.runs.pipeline import RunDeps, RunRequest, execute_run
from backend.features.runs.planning import Candidates
from backend.features.runs.repository import RunRepository
from sqlalchemy.ext.asyncio import AsyncSession

T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)
GOOD = {"domain_rating": 40, "org_traffic": 9000, "refdomains": 500, "org_keywords": 2000}
QUOTA = {
    "units_limit_workspace": 6_400_000,
    "units_usage_workspace": 0,
    "units_limit_api_key": 1_600_000,
    "units_usage_api_key": 0,
}
#: По одному домену каждого списка — из самих списков. Соцсеть и справочник — те,
#: что не ловит правило платформ по меткам: их добавил список обхода.
MEDIA = sorted(MAJOR_MEDIA)[0]
SOCIAL = sorted(host for host in SOCIAL_DOMAINS if not is_platform(host))[0]
REFERENCE = sorted(host for host in REFERENCE_DOMAINS if not is_platform(host))[0]


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("backend.features.ahrefs.client.asyncio.sleep", instant)


class TestByNameBeforeAhrefs:
    def test_media_social_and_reference_are_cut_by_name(self) -> None:
        assert by_name([MEDIA, SOCIAL, REFERENCE, "blog.example.test"]) == {
            MEDIA: ExclusionReason.MAJOR_MEDIA,
            SOCIAL: ExclusionReason.PLATFORM,
            REFERENCE: ExclusionReason.REFERENCE,
        }

    def test_the_report_names_them_in_words(self) -> None:
        assert ExclusionReason.MAJOR_MEDIA.caption == "крупное СМИ"
        assert ExclusionReason.REFERENCE.caption == "справочник или исследования"

    async def test_the_run_gate_does_not_pay_for_media(self, session: AsyncSession) -> None:
        found = await Exclusions(session).excluded_hosts([MEDIA, "blog.example.test"])

        assert found == {MEDIA: ExclusionReason.MAJOR_MEDIA}


class TestBigSiteAtTheFirstStep:
    def test_over_eighty_is_unsuitable_in_words(self) -> None:
        verdict = check_dr(85, T)

        assert verdict is not None
        assert (verdict.status, verdict.reason) == (
            DonorStatus.UNSUITABLE,
            "DR > 80 — крупным сайтам не пишем",
        )

    def test_eighty_is_not_big(self) -> None:
        """«DR > 80» — строго больше, как у обхода (`crawl.big_sites.MAX_DR`)."""
        assert check_dr(80, T) is None

    async def test_big_site_costs_only_the_screen(self) -> None:
        calls: list[str] = []

        def ahrefs(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            calls.append("screen" if len(payload["select"]) == 2 else "full")
            rows = [{"url": "big.example.test/", "domain_rating": 91}]
            return httpx.Response(200, json={"domains": rows})

        http = httpx.AsyncClient(transport=httpx.MockTransport(ahrefs), base_url="https://a.test")
        out: list[DomainResult] = []
        async for batch in collect(
            ["big.example.test"], AhrefsClient(api_key="k", http=http), T, "us", date="2026-10-11"
        ):
            out.extend(batch)

        assert calls == ["screen"], "ни полных метрик, ни стран крупному сайту"
        assert (out[0].status, out[0].reason) == (DonorStatus.UNSUITABLE, big_site_reason(80))


def _ahrefs(dr: dict[str, int]) -> AhrefsClient:
    def answer(request: httpx.Request) -> httpx.Response:
        cost = {"x-api-units-cost-total-actual": "10"}
        if request.url.path.endswith("limits-and-usage"):
            return httpx.Response(200, json={"limits_and_usage": QUOTA})
        hosts = [t["url"] for t in json.loads(request.content)["targets"]]
        rows = [{"url": f"{h}/", **GOOD, "domain_rating": dr[h]} for h in hosts]
        return httpx.Response(200, json={"domains": rows}, headers=cost)

    http = httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="https://a.test")
    return AhrefsClient(api_key="k", http=http)


class NoSerp:
    name = "none"

    async def search(self, *args: object, **kwargs: object) -> dict[str, Any]:
        raise AssertionError("выдача уже куплена — источник не зовут")


async def _settings_id(session: AsyncSession) -> int:
    settings = await RunRepository(session).create_settings(
        T, geo_top_n=5, geo_min_share=0.2, metrics_ttl_days=90, price_ttl_days=150, units_cap=10**5
    )
    return settings.id


class TestTheRunReport:
    async def test_big_sites_have_their_own_reason_in_the_run(self, session: AsyncSession) -> None:
        """«DR > 80» — не «DR ниже порога»: в отчёте прогона своим ключом."""
        hosts = ["big.example.test", "weak.example.test"]
        deps = RunDeps(
            provider=NoSerp(),
            client=_ahrefs({"big.example.test": 91, "weak.example.test": 5}),
            donors=DonorRepository(session),
            runs=RunRepository(session),
            exclusions=Exclusions(session),
        )
        bought = Candidates(hosts=hosts, keywords=1, results=2, empty_keywords=[], dropped=0)

        report = await execute_run(
            deps, RunRequest(["crm"], "us", T, await _settings_id(session), candidates=bought)
        )

        assert report.reject_reasons == {"DR > 80": 1, "DR": 1}


async def _donor(
    session: AsyncSession,
    host: str,
    *,
    dr: int | None = 40,
    review: str | None = "accepted",
    email: str | None = None,
) -> DomainModel:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    session.add(DonorModel(domain_id=domain.id, status=DonorStatus.SUITABLE, dr=dr, review=review))
    if email is not None:
        session.add(ContactModel(domain_id=domain.id, email=email, source=ContactSource.PAGE))
    await session.flush()
    return domain


class TestTheQueueDoesNotOfferThem:
    async def test_big_site_judged_suitable_before_the_rule_is_not_queued(
        self, session: AsyncSession
    ) -> None:
        """Годен по замеру до 11.10.2026 и ещё свеж — не перемерен; DR > 80 — им не пишем,
        и предлагать его человеку незачем. Сколько таких — в отчёте прогона."""
        await _donor(session, "big.example.test", dr=94, review=None)
        await _donor(session, "blog.example.test", dr=45, review=None)
        run = await RunRepository(session).create_run(
            stage=Stage.DONORS,
            settings_id=await _settings_id(session),
            keywords=["crm"],
            country="us",
            status=RunStatus.DONE,
        )

        report = await RunReview(session).queue_run(
            run.id, ["big.example.test", "blog.example.test"]
        )

        assert (report.pending, report.not_writing) == (1, 1)
        assert await RunReview(session).pending_hosts(run.id) == ["blog.example.test"]


class TestLettersDoNotTakeThem:
    async def test_accepted_big_sites_and_media_get_no_letter(self, session: AsyncSession) -> None:
        """Приняли раньше, когда крупные стояли полкой, — письма всё равно нет."""
        await _donor(session, "big.example.test", dr=88, email="editor@big.example.test")
        await _donor(session, MEDIA, dr=None, email="desk@media.example.test")
        await _donor(session, "blog.example.test", dr=45, email="editor@blog.example.test")

        picked = await Recipients(session).donor_candidates(limit=10)

        assert [one.host for one in picked] == ["blog.example.test"]

    async def test_the_funnel_names_the_step(self, session: AsyncSession) -> None:
        await _donor(session, "big.example.test", dr=88, email="editor@big.example.test")
        await _donor(session, "blog.example.test", dr=45, email="editor@blog.example.test")

        funnel = await LetterRepository(session).funnel_report(Stage.DONORS)

        assert funnel == {
            "подходящих": 2,
            "принятых": 2,
            "вне «кому не пишем»": 1,
            "с адресом": 1,
            "вне стоп-листа": 1,
            "ещё не писали": 1,
        }

    async def test_without_them_the_funnel_has_no_extra_step(self, session: AsyncSession) -> None:
        await _donor(session, "blog.example.test", dr=45, email="editor@blog.example.test")

        funnel = await LetterRepository(session).funnel_report(Stage.DONORS)

        assert "вне «кому не пишем»" not in funnel

    async def test_the_card_says_why_in_words(self, session: AsyncSession) -> None:
        big = await _donor(session, "big.example.test", dr=88, email="editor@big.example.test")
        media = await _donor(session, MEDIA, dr=None, email="desk@media.example.test")
        recipients = Recipients(session)

        assert (await recipients.letter_address(big.id)).blocked == (
            f"письма не собираются: {big_site_reason(80)}"
        )
        assert (await recipients.letter_address(media.id)).blocked == (
            f"письма не собираются: {NOT_WRITING_WORDS[ExclusionReason.MAJOR_MEDIA]}"
        )

    async def test_unknown_dr_is_not_big(self, session: AsyncSession) -> None:
        """Донор, заведённый руками, без замера: неизвестный DR не повод вычеркнуть."""
        await _donor(session, "manual.example.test", dr=None, email="editor@manual.example.test")

        picked = await Recipients(session).donor_candidates(limit=10)

        assert [one.host for one in picked] == ["manual.example.test"]
