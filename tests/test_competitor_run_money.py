"""Прогон по ссылкам конкурентов: деньги списков — на продолжении, под удержанием, у потолков
модели, при отказе одного списка и при цене строки дороже модели. На настоящей базе.

Каждый тест — случай, где без правки ревью 20.10.2026 оплаченные списки терялись или юниты
тратились сверх обещанного. Платного нет: Ahrefs — подменённым транспортом
(`test_competitor_run.Ahrefs`), модель не зовётся — её потолки сверяются по журналу.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from backend.config.judge import JudgeMode
from backend.config.topics import TopicMode
from backend.features.ahrefs import refdomains as links
from backend.features.ahrefs.units import estimate_run
from backend.features.core.domain import RunStatus
from backend.features.core.models.run import RunModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.donors.repository import DonorRepository
from backend.features.runs.budget import CapExceededError, QuotaUnavailableError
from backend.features.runs.competitor_estimate import JUDGE_TOKENS_PER_DOMAIN, TRIM
from backend.features.runs.competitor_run import Prepared, held, lists_paid, prepare
from backend.features.runs.competitors import CompetitorLinksError
from backend.features.runs.exclusions import Exclusions
from backend.features.runs.pipeline import RunDeps, RunRequest, execute_run
from backend.features.runs.planning import TRIM as KEYWORDS_TRIM
from backend.features.runs.repository import RunRepository
from backend.features.topics.cost import ESTIMATE as TOPIC_ESTIMATE
from backend.shared.numbers import spaced
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_competitor_run import PLENTY, Ahrefs, NoSerp, T, _rows, _run, _usage

#: Два конкурента, по сайту в списке: оба сайта — не платформы, их пропустит гейт исключений.
LISTS = {"example.com": _rows("example.org"), "example.net": _rows("example.co.uk")}
#: Худший случай до первого списка: два списка глубины 10 по 14 юнитов строка и двадцать
#: сайтов воронки, каждому — страны.
WORST = 2 * links.list_units(10, 14) + estimate_run(20, country_share=1.0).total
#: Подменённый Ahrefs списывает за запрос списка минимум — 50 юнитов (`LIST_COST`).
TWO_LISTS = 100


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Судья выключен — модель не зовётся; паузы повторов не ждутся."""

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.OFF)
    monkeypatch.setattr("backend.features.ahrefs.client.asyncio.sleep", instant)


class Limits(Ahrefs):
    """Остаток отвечает по номеру вопроса: число — код отказа, словарь — другой остаток."""

    def __init__(self, *args: Any, answers: dict[int, int | dict[str, int]], **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.answers = answers
        self.asked_limits = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("limits-and-usage"):
            self.asked_limits += 1
            answer = self.answers.get(self.asked_limits)
            if isinstance(answer, int):
                return httpx.Response(answer, text="busy")
            if answer is not None:
                return httpx.Response(200, json={"limits_and_usage": answer})
        return super().handler(request)


def _deps(session: AsyncSession, client: Any) -> RunDeps:
    return RunDeps(
        provider=NoSerp(),
        client=client,
        donors=DonorRepository(session),
        runs=RunRepository(session),
        exclusions=Exclusions(session),
    )


def _request(run: RunModel, prepared: Prepared) -> RunRequest:
    """Запрос воронке — как его собирает задача (`workers/run_job`)."""
    return RunRequest(
        keywords=[],
        country="us",
        thresholds=T,
        settings_id=run.settings_id,
        cap=prepared.cap,
        run=run,
        candidates=prepared.candidates,
        spent_before=prepared.spent,
    )


class TestResume:
    async def test_resume_after_saved_candidates_keeps_the_lists_in_the_estimate(
        self, session: AsyncSession
    ) -> None:
        """Кандидаты сохранены, смета — ещё нет (Ahrefs не ответил об остатке): продолжение
        не покупает списки, а смета прогона и его счёт списков — с ними (проба ревью)."""
        run = await _run(session)
        first = Limits(LISTS, answers={2: 503})  # 1 — до первого списка, 2 — у воронки
        client = first.client()
        prepared = await prepare(session, client, run, T, cap=50_000)
        assert (prepared.spent, prepared.cap) == (TWO_LISTS, 50_000 - TWO_LISTS)
        with pytest.raises(QuotaUnavailableError):
            await execute_run(_deps(session, client), _request(run, prepared))
        assert run.candidates, "кандидаты сохранены первой попыткой"
        assert held(run), "до точной сметы прогон держит худший случай"

        # Продолжение: потолок задачи — от обещанного за вычетом прошлых списков
        # (`lists_paid`, так считает задача); воронке — он же: новых списков нет.
        second = Ahrefs(LISTS)
        client = second.client()
        assert lists_paid(run) == TWO_LISTS
        again = await prepare(session, client, run, T, cap=50_000 - lists_paid(run))
        report = await execute_run(_deps(session, client), _request(run, again))

        assert again == Prepared(None, 50_000 - TWO_LISTS, TWO_LISTS)
        assert second.asked == [], "списки второй раз не покупаются"
        stored = await session.get(RunModel, run.id)
        assert stored is not None
        assert stored.status is RunStatus.DONE
        assert stored.estimated_units == TWO_LISTS + report.plan.estimate.total
        assert not held(stored)
        assert stored.actual_units == sum(units or 0 for _, units in await _usage(session, run.id))

    async def test_precise_estimate_takes_every_site_to_countries(
        self, session: AsyncSession
    ) -> None:
        """Доля стран точной сметы — как у сметы до запуска, а не из истории прогонов по
        ключам: у той другая выборка сайтов (проба ревью)."""
        run = await _run(session)
        ahrefs = Ahrefs(LISTS)
        client = ahrefs.client()
        prepared = await prepare(session, client, run, T, cap=50_000)

        report = await execute_run(_deps(session, client), _request(run, prepared))

        assert await RunRepository(session).country_call_share("us") < 1.0
        assert report.plan.country_share == 1.0
        assert report.plan.estimate == estimate_run(2, country_share=1.0)


class TestStopAfterLists:
    async def test_refusal_of_the_precise_estimate_speaks_of_competitors_and_keeps_the_fact(
        self, session: AsyncSession
    ) -> None:
        """Соседняя система съела остаток, пока шли списки: точная смета отказывает словами
        этого прогона, а остановленный прогон несёт факт — оплаченные списки (проба ревью)."""
        run = await _run(session)
        tight = {**PLENTY, "units_limit_api_key": 1_000, "units_usage_api_key": 900}
        ahrefs = Limits(LISTS, answers={2: tight})
        client = ahrefs.client()
        prepared = await prepare(session, client, run, T, cap=50_000)

        with pytest.raises(CapExceededError) as caught:
            await execute_run(_deps(session, client), _request(run, prepared))
        runs = RunRepository(session)
        await runs.stop_run(run, stats={"причина": "остановлен"})

        assert str(caught.value).endswith(f"{TRIM}.")
        assert KEYWORDS_TRIM not in str(caught.value)
        assert run.status is RunStatus.STOPPED
        assert run.actual_units == TWO_LISTS, "списки оплачены — факт прогона их называет"
        assert await runs.claimed_units() == 0, "остановленный прогон ничего не держит"


class TestOneListRefused:
    @pytest.mark.parametrize("status", [400, 404, 422])
    async def test_list_refused_for_its_target_after_a_received_one_is_skipped(
        self, session: AsyncSession, status: int
    ) -> None:
        """Запрос Ahrefs уже принял — не подошла цель: пропуск с причиной, а оплаченный
        список первого конкурента идёт в воронку."""
        run = await _run(session)
        ahrefs = Ahrefs(LISTS, refuse={"example.net": status})

        prepared = await prepare(session, ahrefs.client(), run, T, cap=50_000)

        assert prepared.candidates is not None
        assert prepared.candidates.hosts == ["example.org"]
        (skipped,) = prepared.candidates.source["skipped"]
        assert skipped["target"] == "example.net"
        assert f"код {status}" in skipped["reason"]
        assert prepared.spent == TWO_LISTS, "отказ тоже списан — смета его видит"
        assert await _usage(session, run.id) == [("refdomains", 50), ("refdomains", 50)]

        run.candidates = None  # воркер умер до сохранения кандидатов
        again = Ahrefs(LISTS)
        resumed = await prepare(session, again.client(), run, T, cap=50_000)
        assert again.asked == [], "пропущенный не спрашивается снова"
        assert resumed.candidates is not None
        assert resumed.candidates.source["skipped"] == [skipped]

    async def test_same_refusal_on_the_first_list_stops_the_run(
        self, session: AsyncSession
    ) -> None:
        """Ни одного полученного списка — отказ, может быть, про сам запрос: остановка."""
        run = await _run(session)
        ahrefs = Ahrefs(LISTS, refuse={"example.com": 404})

        with pytest.raises(CompetitorLinksError) as caught:
            await prepare(session, ahrefs.client(), run, T, cap=50_000)

        assert caught.value.permanent
        assert ahrefs.asked == ["example.com"]

    @pytest.mark.parametrize("status", [401, 402, 403])
    async def test_key_and_access_refusals_stop_even_after_a_received_list(
        self, session: AsyncSession, status: int
    ) -> None:
        run = await _run(session)
        ahrefs = Ahrefs(LISTS, refuse={"example.net": status})

        with pytest.raises(CompetitorLinksError) as caught:
            await prepare(session, ahrefs.client(), run, T, cap=50_000)

        assert caught.value.status == status
        stored = run.competitors or {}
        assert list(stored["fetched"]) == ["example.com"], "полученный список сохранён"
        assert stored["skipped"] == {}


class TestHold:
    async def test_lists_are_bought_under_a_hold_of_the_worst_case(
        self, session: AsyncSession
    ) -> None:
        """Пока идут списки, соседний прогон видит худший случай этого: точной сметы ещё
        нет, а юниты уже тратятся. Точная смета ложится поверх."""
        run = await _run(session)
        seen: list[tuple[RunStatus, int | None]] = []

        class Watched(Ahrefs):
            def handler(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == links.PATH:
                    seen.append((run.status, run.estimated_units))
                return super().handler(request)

        client = Watched(LISTS).client()
        prepared = await prepare(session, client, run, T, cap=50_000)
        runs = RunRepository(session)

        assert seen == [(RunStatus.RUNNING, WORST)] * 2, "«идёт» и удержание — до списка"
        assert held(run)
        assert await runs.claimed_units() == WORST - TWO_LISTS, "держит то, что не потрачено"

        report = await execute_run(_deps(session, client), _request(run, prepared))

        assert not held(run)
        assert run.estimated_units == TWO_LISTS + report.plan.estimate.total

    async def test_the_hold_is_counted_by_the_ceiling_of_a_neighbour(
        self, session: AsyncSession
    ) -> None:
        run = await _run(session)
        await prepare(session, Ahrefs(LISTS).client(), run, T, cap=50_000)
        neighbour = await _run(session)

        claimed = await RunRepository(session).claimed_units(exclude_run_id=neighbour.id)

        assert claimed == WORST - TWO_LISTS


class TestModelCaps:
    @pytest.fixture(autouse=True)
    def _judge_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.SHADOW)

    async def test_run_cap_of_the_model_refuses_before_the_first_list(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Судья встал бы посреди суда — с оплаченными списками: отказ до первого юнита."""
        monkeypatch.setattr("backend.config.llm.RUN_TOKEN_CAP", 10_000)
        run = await _run(session)
        ahrefs = Ahrefs(LISTS)

        with pytest.raises(LlmCapExceededError) as caught:
            await prepare(session, ahrefs.client(), run, T, cap=50_000)

        words = str(caught.value)
        assert f"обойдутся модели до {spaced(20 * JUDGE_TOKENS_PER_DOMAIN)} токенов" in words
        assert f"потолок модели на прогон — {spaced(10_000)}" in words
        assert TRIM in words
        assert "LLM_RUN_TOKEN_CAP" not in words, "имя настройки — журналу, не человеку"
        assert ahrefs.asked == []
        assert await _usage(session, run.id) == []

    async def test_day_cap_counts_what_the_day_already_spent(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("backend.config.llm.DAILY_TOKEN_CAP", 50_000)
        runs = RunRepository(session)
        await runs.record_tokens(run_id=None, operation="site_judge", tokens=20_000)
        run = await _run(session)
        ahrefs = Ahrefs(LISTS)

        with pytest.raises(LlmCapExceededError, match="остаток дневного потолка модели"):
            await prepare(session, ahrefs.client(), run, T, cap=50_000)

        assert ahrefs.asked == []

    async def test_topics_of_the_sites_count_with_the_judge(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Тема сайта определяется той же пачкой судьи и тратит те же потолки: суд один
        помещается (20 × 1 800), с тематикой — уже нет (+ 20 × оценка на сайт)."""
        monkeypatch.setattr("backend.config.topics.MODE", TopicMode.ON)
        monkeypatch.setattr("backend.config.llm.RUN_TOKEN_CAP", 50_000)
        run = await _run(session)
        ahrefs = Ahrefs(LISTS)

        with pytest.raises(LlmCapExceededError) as caught:
            await prepare(session, ahrefs.client(), run, T, cap=50_000)

        tokens = 20 * (JUDGE_TOKENS_PER_DOMAIN + TOPIC_ESTIMATE.tokens)
        assert f"обойдутся модели до {spaced(tokens)} токенов" in str(caught.value)
        assert ahrefs.asked == []

    async def test_caps_with_room_let_the_lists_go(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("backend.config.llm.RUN_TOKEN_CAP", 1_000_000)
        monkeypatch.setattr("backend.config.llm.DAILY_TOKEN_CAP", 1_000_000)
        run = await _run(session)
        ahrefs = Ahrefs(LISTS)

        prepared = await prepare(session, ahrefs.client(), run, T, cap=50_000)

        assert prepared.candidates is not None
        assert ahrefs.asked == ["example.com", "example.net"]


class TestDearerRows:
    async def test_dearer_row_than_the_model_rechecks_the_budget_before_the_next_list(
        self, session: AsyncSession
    ) -> None:
        """Ahrefs списал за строку 30 юнитов вместо 14: второй список с той же глубиной
        дороже сметы, и бюджет сверяется заново до него, а не после."""
        run = await _run(session)
        dear = {
            "x-api-units-cost-total-actual": "300",
            "x-api-units-cost-total": "300",
            "x-api-units-cost-row": "30",
        }
        ten = _rows(*(f"site{index}.github.io" for index in range(10)))

        class Dearer(Ahrefs):
            def handler(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == links.PATH and request.url.params["target"] == "example.com":
                    self.asked.append("example.com")
                    return httpx.Response(200, json={"refdomains": ten}, headers=dear)
                return super().handler(request)

        ahrefs = Dearer({"example.net": _rows("example.co.uk")})

        with pytest.raises(CapExceededError) as caught:
            await prepare(session, ahrefs.client(), run, T, cap=WORST)

        assert "Строка списка у Ahrefs дороже сметы: 30 юнитов вместо 14" in str(caught.value)
        assert ahrefs.asked == ["example.com"], "второй список не куплен"
        assert await _usage(session, run.id) == [("refdomains", 300)]
        assert list((run.competitors or {})["fetched"]) == ["example.com"]

    async def test_dearer_row_that_still_fits_goes_on(self, session: AsyncSession) -> None:
        run = await _run(session)
        dear = {"x-api-units-cost-total-actual": "50", "x-api-units-cost-row": "30"}

        class Dearer(Ahrefs):
            def handler(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == links.PATH and request.url.params["target"] == "example.com":
                    self.asked.append("example.com")
                    return httpx.Response(
                        200, json={"refdomains": LISTS["example.com"]}, headers=dear
                    )
                return super().handler(request)

        ahrefs = Dearer(LISTS)

        prepared = await prepare(session, ahrefs.client(), run, T, cap=50_000)

        assert ahrefs.asked == ["example.com", "example.net"]
        assert prepared.candidates is not None
        # Удержание пересчитано по цене из ответа: второй список — по 30 за строку.
        assert (
            run.estimated_units
            == 50 + links.list_units(10, 30) + estimate_run(11, country_share=1.0).total
        )
