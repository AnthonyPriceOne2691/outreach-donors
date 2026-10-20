"""Во что обходится тема сайта: токены на сайт — из истории прогонов, деньги — по прайсу,
и смета прогона называет их до запуска.

Числа здесь — арифметика сметы, а не замер модели: замер даёт ветка `topics` отчёта
прогона, а до первого прогона с темами смета честно говорит «оценка».
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.config import judge as judge_cfg
from backend.config import topics as topics_cfg
from backend.features.core.domain import RunStatus, Stage, UserRole
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from backend.features.topics.cost import (
    ESTIMATE,
    HISTORY_WINDOW,
    MIN_ANSWERS,
    PerSite,
    per_site,
    per_site_from,
    runs_classify,
)
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_ahrefs_cap import known_key


def _run(
    classified: int,
    tokens: int,
    *,
    unclear: int = 0,
    unreadable: int = 0,
    prompt: int = 0,
    completion: int = 0,
) -> dict[str, Any]:
    return {
        "topics": {
            "classified": classified,
            "unclear": unclear,
            "unreadable": unreadable,
            "tokens": tokens,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
        }
    }


class TestPerSite:
    def test_without_history_it_is_an_estimate_and_says_so(self) -> None:
        assert per_site_from([]) is ESTIMATE
        assert not ESTIMATE.measured
        assert per_site_from([{"serp_results": 40}, {"topics": "битый"}]) is ESTIMATE

    def test_a_run_gives_tokens_per_answer_with_its_split(self) -> None:
        found = per_site_from([_run(20, 40_000, unclear=5, prompt=31_250, completion=8_750)])
        assert found == PerSite(prompt=1_250, completion=350, measured=True)
        assert found.tokens == 1_600

    def test_an_answer_out_of_form_is_a_paid_answer(self) -> None:
        """Ответ не по форме оплачен, как названная тема: токены — в счёт, и ответ — тоже."""
        found = per_site_from([_run(15, 40_000, unreadable=5, prompt=31_250, completion=8_750)])
        assert found.tokens == 2_000

    def test_the_estimate_covers_the_prompt_and_the_worst_script(self) -> None:
        """Оценка до замера — по худшему письму: промпт со списком тем (~1030 токенов) и
        текст сайта в потолке (1500 знаков кириллицы — до ~590)."""
        assert (ESTIMATE.prompt, ESTIMATE.completion) == (1_700, 300)
        assert ESTIMATE.usd(1) == pytest.approx(0.001025)

    def test_the_worst_recent_run_wins(self) -> None:
        """Смета — верхняя граница: средняя занизила бы потолок модели посреди прогона."""
        found = per_site_from(
            [
                _run(20, 20_000, prompt=16_000, completion=4_000),
                _run(20, 50_000, prompt=40_000, completion=10_000),
            ]
        )
        assert found.tokens == 2_500

    def test_a_small_run_is_noise(self) -> None:
        small = _run(MIN_ANSWERS - 1, 90_000, prompt=80_000, completion=10_000)
        assert per_site_from([small]) is ESTIMATE

    def test_without_the_split_the_total_is_shared_like_the_estimate(self) -> None:
        found = per_site_from([_run(10, 20_000)])
        assert found.tokens == 2_000
        assert (found.prompt, found.completion) == (1_700, 300)

    def test_only_the_window_is_read(self) -> None:
        recent = [_run(20, 20_000, prompt=16_000, completion=4_000)] * HISTORY_WINDOW
        found = per_site_from([*recent, _run(20, 200_000, prompt=160_000, completion=40_000)])
        assert found.tokens == 1_000, "прогон за окном истории в смету не идёт"

    def test_money_is_input_and_output_at_their_prices(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(topics_cfg, "INPUT_USD_PER_1M_TOKENS", 0.25)
        monkeypatch.setattr(topics_cfg, "OUTPUT_USD_PER_1M_TOKENS", 2.0)
        cost = PerSite(prompt=1_700, completion=300, measured=False)
        assert cost.usd(1_000) == pytest.approx(1_000 * (1_700 * 0.25 + 300 * 2.0) / 1e6)
        assert cost.usd(-5) == 0


@pytest.mark.parametrize(
    ("topics", "judge", "classify"),
    [
        (topics_cfg.TopicMode.ON, judge_cfg.JudgeMode.SHADOW, True),
        (topics_cfg.TopicMode.ON, judge_cfg.JudgeMode.ENFORCE, True),
        (topics_cfg.TopicMode.ON, judge_cfg.JudgeMode.OFF, False),
        (topics_cfg.TopicMode.OFF, judge_cfg.JudgeMode.SHADOW, False),
    ],
)
def test_a_run_classifies_only_next_to_the_judge(
    monkeypatch: pytest.MonkeyPatch,
    topics: topics_cfg.TopicMode,
    judge: judge_cfg.JudgeMode,
    classify: bool,
) -> None:
    monkeypatch.setattr(topics_cfg, "MODE", topics)
    monkeypatch.setattr(judge_cfg, "MODE", judge)
    assert runs_classify() is classify


async def _done_run(session: AsyncSession, stats: dict[str, Any]) -> None:
    repository = RunRepository(session)
    settings = await repository.create_settings(
        defaults(),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    run = await repository.create_run(
        stage=Stage.DONORS,
        settings_id=settings.id,
        keywords=["k"],
        country="us",
        status=RunStatus.DONE,
    )
    run.stats = stats
    await session.commit()


async def test_history_is_read_from_finished_runs(session: AsyncSession) -> None:
    await _done_run(session, _run(20, 40_000, prompt=30_000, completion=10_000))

    assert (await per_site(session)).tokens == 2_000


class _NoAhrefs:
    async def aclose(self) -> None:
        return None


@pytest.fixture
async def estimating(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, make_user: Any, sign_in: Any
) -> str:
    """Смета без сети: остаток у Ahrefs большой, ключ известен; пропуск оператора."""

    async def plenty(_: Any, **__: Any) -> int:
        return 10_000_000

    await known_key(session, key_limit=100_000)
    monkeypatch.setattr("backend.api.runs.routes.AhrefsClient", _NoAhrefs)
    monkeypatch.setattr("backend.api.runs.routes.units_left", plenty)
    monkeypatch.setattr("backend.config.ahrefs.API_KEY", "ключ-для-теста")
    monkeypatch.setattr("backend.config.serp.SANDBOX", False)
    await make_user("оператор@team.example.test", role=UserRole.OPERATOR)
    token: str = await sign_in("оператор@team.example.test")
    return token


BODY = {"keywords": ["ключ 1", "ключ 2"], "country": "us"}


async def test_estimate_names_topic_tokens_measured_by_past_runs(
    client: AsyncClient,
    estimating: str,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(topics_cfg, "MODE", topics_cfg.TopicMode.ON)
    monkeypatch.setattr(judge_cfg, "MODE", judge_cfg.JudgeMode.SHADOW)
    await _done_run(session, _run(20, 40_000, prompt=30_000, completion=10_000))

    response = await client.post("/api/runs/estimate", json=BODY, headers=bearer(estimating))

    assert response.status_code == 200, response.text
    made = response.json()
    assert made["topic_tokens_per_site"] == 2_000
    assert made["topic_tokens"] == made["expected_domains"] * 2_000
    assert made["topic_measured"] is True
    assert made["units_total"] > 0, "юниты Ahrefs — своим счётом, токены в них не входят"


async def test_estimate_without_topics_names_none(
    client: AsyncClient, estimating: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тематика выключена — токенов тематики в смете нет: ноль, а не оценка."""
    monkeypatch.setattr(topics_cfg, "MODE", topics_cfg.TopicMode.OFF)

    response = await client.post("/api/runs/estimate", json=BODY, headers=bearer(estimating))

    assert response.status_code == 200, response.text
    assert (response.json()["topic_tokens"], response.json()["topic_tokens_per_site"]) == (0, 0)
