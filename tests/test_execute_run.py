"""Прогон целиком, на настоящей базе. Проверяется в первую очередь то,
что должно уцелеть при сбое."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from backend.config.judge import JudgeMode
from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.core.domain import DonorStatus, RunStatus, SuppressionReason
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import SuppressionModel, UsageRecordModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.donors.publisher_judge import Intent, Judgement, Recommendation
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Thresholds
from backend.features.review.candidates import RunReview
from backend.features.runs.exclusions import ExclusionReason, Exclusions
from backend.features.runs.pipeline import RunDeps, RunRequest, execute_run
from backend.features.runs.repository import RunRepository
from backend.features.serp.protocol import SerpResult
from backend.shared.logs import current_run_id
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)
QUOTA = {
    "units_limit_workspace": 8_000_000,
    "units_usage_workspace": 0,
    "units_limit_api_key": 2_000_000,
    "units_usage_api_key": 0,
}
GOOD = {"domain_rating": 40, "org_traffic": 9000, "refdomains": 500, "org_keywords": 2000}


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тесты не ждут пауз между повторами: проверяется поведение, а не часы."""

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("backend.features.ahrefs.client.asyncio.sleep", instant)


class FakeSerp:
    name = "fake"

    def __init__(self, urls: list[str]) -> None:
        self._urls = urls

    async def search(
        self, keywords: Sequence[str], country: str, *, depth_pages: int = 1
    ) -> dict[str, list[SerpResult]]:
        return {kw: [SerpResult(i + 1, u) for i, u in enumerate(self._urls)] for kw in keywords}


def _ahrefs(
    metrics: dict[str, dict[str, Any]],
    *,
    fail_after_screen: bool = False,
    watch: list[str] | None = None,
) -> AhrefsClient:
    """Заглушка провайдера.

    `fail_after_screen` роняет второй пакетный запрос окончательной ошибкой:
    просев уже оплачен, а прогон дальше не идёт. Падения на запросе по странам
    для этого не годятся — их коллектор обрабатывает штатно и прогон
    не останавливает.

    `watch` копит домены, о которых провайдера спросили. Список ответов
    на вопрос «за кого мы заплатили» — единственный способ увидеть,
    что ступень отбора стоит перед платной, а не после.
    """
    batches = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        cost = {"x-api-units-cost-total-actual": "10"}
        if path.endswith("limits-and-usage"):
            return httpx.Response(200, json={"limits_and_usage": QUOTA})
        if path.endswith("batch-analysis"):
            batches["n"] += 1
            if fail_after_screen and batches["n"] == 2:
                return httpx.Response(403, text="forbidden", headers=cost)
            hosts = [t["url"] for t in json.loads(request.content)["targets"]]
            if watch is not None:
                watch.extend(hosts)
            rows = [{"url": f"{h}/", **metrics[h]} for h in hosts if h in metrics]
            return httpx.Response(200, json={"domains": rows}, headers=cost)
        return httpx.Response(
            200, json={"metrics": [{"country": "us", "org_traffic": 8000}]}, headers=cost
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")
    return AhrefsClient(api_key="k", http=http)


async def _deps(session: AsyncSession, serp: FakeSerp, client: AhrefsClient) -> RunDeps:
    return RunDeps(
        provider=serp,
        client=client,
        donors=DonorRepository(session),
        runs=RunRepository(session),
        exclusions=Exclusions(session),
    )


async def _settings_id(session: AsyncSession) -> int:
    settings = await RunRepository(session).create_settings(
        T,
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    return settings.id


class TestTheCostOfSearch:
    async def test_the_search_lands_in_the_journal(self, session: AsyncSession) -> None:
        """Выдача платится деньгами, и до этого среза её расход
        не записывался вовсе: экран показывал по ней ноль."""

        class Paid(FakeSerp):
            spent = 0.0

            async def search(
                self, keywords: Sequence[str], country: str, *, depth_pages: int = 1
            ) -> dict[str, list[SerpResult]]:
                self.spent += 0.12
                return await super().search(keywords, country, depth_pages=depth_pages)

        deps = await _deps(session, Paid(["https://good.com"]), _ahrefs({"good.com": GOOD}))

        await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

        rows = (
            (
                await session.execute(
                    select(UsageRecordModel).where(UsageRecordModel.operation == "serp_search")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].amount_usd is not None
        assert float(rows[0].amount_usd) == pytest.approx(0.12)
        assert rows[0].run_id is not None


class TestHappyPath:
    async def test_run_produces_donors_and_a_closed_record(self, session: AsyncSession) -> None:
        deps = await _deps(
            session, FakeSerp(["https://www.good.com/x"]), _ahrefs({"good.com": GOOD})
        )
        report = await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

        assert report.by_status[DonorStatus.SUITABLE] == 1

        run = (await session.execute(select(RunModel))).scalar_one()
        assert run.status is RunStatus.DONE
        assert run.stats["unique_hosts"] == 1
        assert run.stats["checked_now"] == 1

        donor = (await session.execute(select(DonorModel))).scalar_one()
        assert donor.status is DonorStatus.SUITABLE

    async def test_second_run_pays_for_nothing(self, session: AsyncSession) -> None:
        """Главное обещание . Проверяется на базе, а не логикой."""
        serp, client = FakeSerp(["https://good.com"]), _ahrefs({"good.com": GOOD})
        settings_id = await _settings_id(session)

        first = await execute_run(
            await _deps(session, serp, client), RunRequest(["crm"], "us", T, settings_id)
        )
        second = await execute_run(
            await _deps(session, serp, client), RunRequest(["crm"], "us", T, settings_id)
        )

        assert first.plan.estimate.total > 0
        assert second.plan.estimate.total == 0
        assert second.plan.new == []
        assert len(second.plan.fresh) == 1

    async def test_report_compares_estimate_with_fact(self, session: AsyncSession) -> None:
        """Заметное расхождение значит, что цены у провайдера изменились."""
        deps = await _deps(session, FakeSerp(["https://good.com"]), _ahrefs({"good.com": GOOD}))
        report = await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

        run = (await session.execute(select(RunModel))).scalar_one()
        assert run.stats["units_estimated"] > 0
        assert run.stats["units_spent"] == report.spent_units
        assert "estimate_error" in run.stats


class TestFailures:
    async def test_country_failure_does_not_stop_the_run(self, session: AsyncSession) -> None:
        """Падение на третьей ступени обрабатывается штатно: метрики за неё
        уже оплачены, домен помечается «не проверен», прогон продолжается."""

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("limits-and-usage"):
                return httpx.Response(200, json={"limits_and_usage": QUOTA})
            if path.endswith("batch-analysis"):
                return httpx.Response(
                    200,
                    json={"domains": [{"url": "good.com/", **GOOD}]},
                    headers={"x-api-units-cost-total-actual": "10"},
                )
            return httpx.Response(500)

        http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://api.test"
        )
        deps = await _deps(
            session, FakeSerp(["https://good.com"]), AhrefsClient(api_key="k", http=http)
        )
        report = await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

        assert report.by_status[DonorStatus.UNCHECKED] == 1
        run = (await session.execute(select(RunModel))).scalar_one()
        assert run.status is RunStatus.DONE

    async def test_run_is_never_left_hanging(self, session: AsyncSession) -> None:
        """Прогон, навсегда оставшийся «идёт», выглядит как зависший сервис
        и заставляет разбираться руками в базе."""
        deps = await _deps(
            session,
            FakeSerp(["https://good.com"]),
            _ahrefs({"good.com": GOOD}, fail_after_screen=True),
        )

        with pytest.raises(Exception, match="403"):
            await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

        run = (await session.execute(select(RunModel))).scalar_one()
        assert run.status is RunStatus.STOPPED
        assert "403" in run.stats["failure"]

    async def test_spending_before_the_failure_is_recorded(self, session: AsyncSession) -> None:
        """Прогон, упавший на середине, уже потратил — и это должно остаться
        в журнале, иначе разбор «на что ушли юниты» соврёт там, где он нужнее."""
        deps = await _deps(
            session,
            FakeSerp(["https://good.com"]),
            _ahrefs({"good.com": GOOD}, fail_after_screen=True),
        )
        with pytest.raises(Exception, match="403"):
            await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

        spent = (await session.execute(select(UsageRecordModel))).scalars().all()
        assert spent
        assert all(r.units > 0 for r in spent)


class TestRunIsMarkedInLogs:
    """Идентификатор прогона обязан стоять в записях, а не только существовать.

    Механизм пометки — `run_context` — сам по себе ничего не доказывает:
    пока его никто не вызывает, каждая запись уходит с пустым `run_id`, и
    вопрос «что было в прогоне 47» по логам по-прежнему без ответа. Поэтому
    проверяется не наличие обёртки в исходнике, а сама запись из прогона.
    """

    async def test_records_made_during_a_run_carry_its_id(
        self, session: AsyncSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        seen: list[str] = []

        deps = await _deps(
            session, FakeSerp(["https://www.good.com/x"]), _ahrefs({"good.com": GOOD})
        )
        original = deps.donors.save_results

        async def spy(batch: object) -> object:
            # Глубоко внутри прогона, в чужом для пайплайна модуле: если метка
            # держится только на верхнем кадре, здесь её уже не будет.
            seen.append(current_run_id())
            return await original(batch)  # type: ignore[arg-type]

        deps.donors.save_results = spy  # type: ignore[method-assign]
        await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

        run = (await session.execute(select(RunModel))).scalar_one()
        assert seen, "прогон не дошёл до сохранения — проверять нечего"
        assert seen == [str(run.id)] * len(seen)

    async def test_outside_a_run_the_mark_is_empty(self) -> None:
        """Граница честная: вне прогона метки нет, и это не ошибка."""
        assert current_run_id() == ""


class TestTheGateStopsTheRoute:
    """Проверка вызовов, а не результата.

    Домен из стоп-листа и так не получил бы письма — это ловил отбор
    очереди. Дорого другое: до этого среза он успевал пройти просев,
    метрики и страны. Итоговая таблица доноров при перестановке ступеней
    не меняется, меняется счёт, и виден он только по вызовам.
    """

    async def test_a_stop_listed_domain_never_reaches_the_provider(
        self, session: AsyncSession
    ) -> None:
        domain = DomainModel(host="banned.com")
        session.add(domain)
        await session.flush()
        session.add(SuppressionModel(domain_id=domain.id, reason=SuppressionReason.COMPLAINED))
        await session.flush()

        asked: list[str] = []
        client = _ahrefs({"banned.com": GOOD, "good.com": GOOD}, watch=asked)
        deps = await _deps(session, FakeSerp(["https://banned.com", "https://good.com"]), client)

        report = await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

        assert "banned.com" not in asked
        assert "good.com" in asked
        assert report.plan.excluded == {"banned.com": ExclusionReason.STOPLIST}

        run = (await session.execute(select(RunModel))).scalar_one()
        assert run.stats["excluded"] == 1
        assert run.stats["excluded_by_reason"] == {"в стоп-листе": 1}
        assert run.stats["units_saved_by_gate"] > 0


class TestTheJudgeStandsBeforeTheBill:
    """Судья стоит ДО Ahrefs, и в наблюдении он не режет.

    Проверяется не качество вердикта — оно не в нашей власти, — а две вещи,
    которые целиком наши: за кого мы заплатили и кого потеряли.
    """

    class Titled(FakeSerp):
        """Выдача с заголовками: без текста судить не по чему."""

        async def search(
            self, keywords: Sequence[str], country: str, *, depth_pages: int = 1
        ) -> dict[str, list[SerpResult]]:
            return {
                kw: [SerpResult(i + 1, u, title=f"Заголовок {u}") for i, u in enumerate(self._urls)]
                for kw in keywords
            }

    @staticmethod
    def _judge(monkeypatch: pytest.MonkeyPatch, rejects: set[str]) -> None:

        async def fake(http: object, **kwargs: object) -> Judgement:
            host = str(kwargs["host"])
            if host in rejects:
                return Judgement(
                    Intent.SELLS_OWN, Recommendation.REJECT, "Заголовок", "продаёт своё", "m", 400
                )
            return Judgement(
                Intent.REFERS_OUT, Recommendation.ACCEPT, "Заголовок", "обзоры", "m", 400
            )

        monkeypatch.setattr("backend.features.donors.judging.judge_host", fake)
        # ⚠ Без этого проход ходил за главными brand.com и media.com в живую
        # сеть: тесты зеленели только потому, что сеть была.
        monkeypatch.setattr("backend.config.judge.HOME_CHECK", False)

    async def test_shadow_counts_but_does_not_cut(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.SHADOW)
        self._judge(monkeypatch, rejects={"brand.com"})

        paid: list[str] = []
        serp = self.Titled(["https://brand.com/a", "https://media.com/a"])
        client = _ahrefs({"brand.com": GOOD, "media.com": GOOD}, watch=paid)

        report = await execute_run(
            await _deps(session, serp, client),
            RunRequest(
                keywords=["k"],
                country="us",
                thresholds=T,
                settings_id=await _settings_id(session),
            ),
        )

        assert report.judge is not None
        assert report.judge.would_cut == 1, "отрезал бы бренд"
        assert report.judge.units_saved > 0, "и это число обосновывает включение"
        # ⚠ Главное: в наблюдении он НЕ режет. За бренд мы всё равно заплатили.
        assert "brand.com" in paid
        assert {"brand.com", "media.com"} <= set(paid)
        # Токены судьи — строка журнала, как у сборки ключей: без неё
        # журнал показывал, что судья работает даром.
        spent = (
            await session.execute(
                select(UsageRecordModel).where(UsageRecordModel.operation == "site_judge")
            )
        ).scalar_one()
        assert spent.units == 800
        # ⚠ И в «факт» прогона токены не идут: у модели единица — токен,
        # у Ahrefs — юнит. 23.09 они сложились, и факт вышел 26 203 при 901.
        assert report.spent_units == sum(report.spent_by_operation.values())

    async def test_enforce_cuts_before_first_spend(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.ENFORCE)
        self._judge(monkeypatch, rejects={"brand.com"})

        paid: list[str] = []
        serp = self.Titled(["https://brand.com/a", "https://media.com/a"])
        client = _ahrefs({"brand.com": GOOD, "media.com": GOOD}, watch=paid)

        report = await execute_run(
            await _deps(session, serp, client),
            RunRequest(
                keywords=["k"],
                country="us",
                thresholds=T,
                settings_id=await _settings_id(session),
            ),
        )

        assert report.judge is not None
        assert report.judge.would_cut == 1
        # За отрезанный домен Ahrefs не спрашивали вовсе — в этом вся выгода.
        assert "brand.com" not in paid, "заплатили за домен, который сами же отбросили"
        assert "media.com" in paid

    async def test_verdict_lands_on_domain_and_outlives_run(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Вердикт — свойство САЙТА: второму этапу он нужен с обратным знаком."""

        monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.SHADOW)
        self._judge(monkeypatch, rejects={"brand.com"})

        serp = self.Titled(["https://brand.com/a"])
        client = _ahrefs({"brand.com": GOOD})
        await execute_run(
            await _deps(session, serp, client),
            RunRequest(
                keywords=["k"],
                country="us",
                thresholds=T,
                settings_id=await _settings_id(session),
            ),
        )

        row = (
            await session.execute(select(DomainModel).where(DomainModel.host == "brand.com"))
        ).scalar_one()
        assert row.site_intent == "sells_own"
        assert row.judge_recommendation == "reject"
        assert row.judge_source_url == "https://brand.com/a"
        assert row.judged_at is not None
        # Решение человека не трогается судом: расхождение считать не из чего,
        # если пересуд его затирает.
        assert row.human_intent is None

    async def test_a_judge_without_a_key_says_so_and_leaves_no_verdict(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Прогон №21, 24.09.2026: ключа модели на сервере не было, а отчёт
        говорил «судили 59, отрезал бы 0» — неработающий судья выглядел как
        судья, никого не отрезавший, и полгода держал домены без суда."""
        monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.SHADOW)
        monkeypatch.setattr("backend.config.judge.HOME_CHECK", False)
        monkeypatch.setattr("backend.config.llm.API_KEY", "")

        serp = self.Titled(["https://brand.com/a"])
        await execute_run(
            await _deps(session, serp, _ahrefs({"brand.com": GOOD})),
            RunRequest(["k"], "us", T, await _settings_id(session)),
        )

        run = (await session.execute(select(RunModel))).scalar_one()
        judge = run.stats["judge"]
        assert (judge["unanswered"], judge["judged"]) == (1, 0)
        assert "LLM_API_KEY" in judge["unanswered_reason"]
        row = (
            await session.execute(select(DomainModel).where(DomainModel.host == "brand.com"))
        ).scalar_one()
        assert row.judged_at is None, "след сбоя не должен становиться кэшем судьи"
        assert row.judge_recommendation is None


class TestRunEndsInAReviewQueue:
    """Прогон 23.09.2026: пороги признали годными nih.gov и reddit.com,
    а первые письма ушли бы брендам. Теперь прогон кончается очередью."""

    async def test_suitable_waits_for_a_human_and_undisputed_never_reach_ahrefs(
        self, session: AsyncSession
    ) -> None:
        asked: list[str] = []
        serp = FakeSerp(["https://good.com/a", "https://nih.gov/b", "https://www.reddit.com/r/x"])
        deps = await _deps(
            session,
            serp,
            _ahrefs({"good.com": GOOD, "nih.gov": GOOD, "reddit.com": GOOD}, watch=asked),
        )
        deps = replace(deps, review=RunReview(session))

        report = await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

        assert "nih.gov" not in asked
        assert "reddit.com" not in asked
        assert report.review is not None
        assert report.review.pending == 1
        candidate = (await session.execute(select(RunCandidateModel))).scalar_one()
        assert candidate.status == "pending"
        run = (await session.execute(select(RunModel))).scalar_one()
        assert run.stats["review"]["pending"] == 1
        assert run.stats["excluded_by_reason"] == {
            "гос. или учебная зона": 1,
            "платформа или соцсеть": 1,
        }


# --- сбой посередине: повтор и сохранность уже собранного --------------------


def _flaky_ahrefs(metrics: dict[str, dict[str, Any]], *, broken: dict[str, bool]) -> AhrefsClient:
    """Провайдер, у которого пакетный запрос метрик отвечает 503, пока
    `broken["on"]`. Просев (первый пакет) проходит: он уже оплачен, и сбой
    случается на середине — ровно тот случай, где страшно потерять сделанное."""
    batches = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        cost = {"x-api-units-cost-total-actual": "10"}
        if path.endswith("limits-and-usage"):
            return httpx.Response(200, json={"limits_and_usage": QUOTA})
        if path.endswith("batch-analysis"):
            batches["n"] += 1
            if broken["on"] and batches["n"] >= 2:
                return httpx.Response(503, text="upstream down")
            hosts = [t["url"] for t in json.loads(request.content)["targets"]]
            rows = [{"url": f"{h}/", **metrics[h]} for h in hosts if h in metrics]
            return httpx.Response(200, json={"domains": rows}, headers=cost)
        return httpx.Response(
            200, json={"metrics": [{"country": "us", "org_traffic": 8000}]}, headers=cost
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")
    return AhrefsClient(api_key="k", http=http)


async def _mark_collected(session: AsyncSession, host: str) -> None:
    """Домен, чьи метрики уже куплены, — как если бы первая попытка успела
    сохранить свою пачку до сбоя."""
    domain = (
        await session.execute(select(DomainModel).where(DomainModel.host == host))
    ).scalar_one_or_none()
    if domain is None:
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()
    session.add(
        DonorModel(
            domain_id=domain.id,
            status=DonorStatus.SUITABLE,
            dr=40,
            metrics_refreshed_at=datetime.now(UTC),
        )
    )
    await session.flush()


class TestTransientFailureIsRetriedNotBuried:
    async def test_network_minute_leaves_the_run_for_the_next_attempt(
        self, session: AsyncSession
    ) -> None:
        """Сеть, 5xx после всех повторов запроса — это не повод хоронить
        прогон: он остаётся «идёт», и разбор продолжит его с последней точки."""
        broken = {"on": True}
        deps = await _deps(
            session,
            FakeSerp(["https://good.com"]),
            _flaky_ahrefs({"good.com": GOOD}, broken=broken),
        )

        with pytest.raises(AhrefsError) as caught:
            await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

        assert not caught.value.permanent
        run = (await session.execute(select(RunModel))).scalar_one()
        assert run.status is RunStatus.RUNNING
        assert run.stats["причина"].startswith("сбой, будет продолжен: AhrefsError")
        assert run.actual_units is not None
        assert run.actual_units > 0, "просев оплачен и записан"

    async def test_permanent_refusal_still_stops_at_once(self, session: AsyncSession) -> None:
        deps = await _deps(
            session,
            FakeSerp(["https://good.com"]),
            _ahrefs({"good.com": GOOD}, fail_after_screen=True),
        )
        with pytest.raises(AhrefsError) as caught:
            await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

        assert caught.value.permanent, "403 повтором не лечится"
        run = (await session.execute(select(RunModel))).scalar_one()
        assert run.status is RunStatus.STOPPED
        assert run.stats["причина"].startswith("остановлен: AhrefsError")

    async def test_failed_attempts_keep_what_the_run_already_has(
        self, session: AsyncSession
    ) -> None:
        """Упавшая попытка не подтирает сделанное: выдача, первая смета,
        журнал расхода и пометки разбора переживают и второе падение."""
        broken = {"on": True}
        serp = FakeSerp(["https://good.com", "https://also-good.com"])
        metrics = {"good.com": GOOD, "also-good.com": GOOD}
        deps = await _deps(session, serp, _flaky_ahrefs(metrics, broken=broken))
        request = RunRequest(["crm"], "us", T, await _settings_id(session))

        with pytest.raises(AhrefsError):
            await execute_run(deps, request)
        run = (await session.execute(select(RunModel))).scalar_one()
        estimate, hosts = run.estimated_units, list(run.candidates["hosts"])
        spent_first = run.actual_units
        # Так разбор мёртвых помечает продолжение.
        run.stats = {**run.stats, "продолжений": 1}
        # Один домен первая попытка успела собрать: вторая считает смету по
        # остатку — меньше первой — и затереть ею первую нельзя.
        await _mark_collected(session, "also-good.com")
        await session.flush()

        with pytest.raises(AhrefsError):
            await execute_run(
                await _deps(session, serp, _flaky_ahrefs(metrics, broken=broken)),
                replace(request, run=run),
            )
        await session.refresh(run)

        assert run.candidates["hosts"] == hosts, "выдача не покупается и не теряется"
        assert run.estimated_units == estimate, "смета — обещание первой попытки"
        assert run.actual_units >= spent_first, "факт — по журналу за все попытки"
        assert run.stats["продолжений"] == 1, "счётчик разбора не обнуляется"
        assert run.status is RunStatus.RUNNING

    async def test_resumed_run_finishes_with_its_history(self, session: AsyncSession) -> None:
        broken = {"on": True}
        serp = FakeSerp(["https://good.com"])
        deps = await _deps(session, serp, _flaky_ahrefs({"good.com": GOOD}, broken=broken))
        request = RunRequest(["crm"], "us", T, await _settings_id(session))
        with pytest.raises(AhrefsError):
            await execute_run(deps, request)
        run = (await session.execute(select(RunModel))).scalar_one()
        estimate = run.estimated_units
        run.stats = {**run.stats, "продолжений": 1}
        await session.flush()

        broken["on"] = False
        await execute_run(
            await _deps(session, serp, _flaky_ahrefs({"good.com": GOOD}, broken=broken)),
            replace(request, run=run),
        )
        await session.refresh(run)

        assert run.status is RunStatus.DONE
        assert run.estimated_units == estimate
        assert run.stats["продолжений"] == 1
        assert run.stats["причина"].startswith("продолжен после сбоя (1 раз)")
        donor = (await session.execute(select(DonorModel))).scalar_one()
        assert donor.status is DonorStatus.SUITABLE
