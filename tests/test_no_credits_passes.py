"""Модель без кредитов в остальных проходах (слово владельца 20.10.2026, ревью соседа 21.10).

- Арбитр по индексу (`donors/judging.through_index`) встаёт на первом «нет кредитов»: у остальных
  закрытых главных — вердикт модели по выдаче, проход помнит причину (`JudgeSummary.stopped`).
- Сложение пачек суда: «нет кредитов» побеждает прочие причины — в отчёт не уходит тайм-аут.
- Смета прогона по ссылкам конкурентов называет, почему модель не готова (`model_refusal`), как смета
  по ключам.
- Консольный прогон, досуд судьи и досуд тем: модель — до первой траты, одна проверка на запуск;
  «нет кредитов» в пачке — следующие пачки модель не зовут.

Провайдер модели — подставной (`tests/test_model_no_credits.py`), выдача и Ahrefs — подделки тестов
прогона и консоли; домены выдуманы.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.cli.main import main
from backend.config.judge import JudgeMode
from backend.features.core.models.domain import DomainModel
from backend.features.donors import backfill as judge_backfill
from backend.features.donors.backfill import BackfillPlan, backfill
from backend.features.donors.home_signals import HomeSignals
from backend.features.donors.judging import JudgeSummary, judge_candidates, through_index
from backend.features.donors.publisher_judge import Decider, Intent, Judgement, Recommendation
from backend.features.runs.planning import SerpText
from backend.features.topics import backfill as topic_backfill
from backend.features.topics.backfill import TopicPlan, backfill_topics
from backend.features.topics.classifier import SiteText, TopicVerdict
from backend.features.topics.cost import PerSite
from backend.features.topics.step import TopicSummary
from backend.shared.llm import NO_CREDITS_WORDS, ModelUnreadyError
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_ahrefs_cap import known_key
from tests.test_api_competitor_runs import BODY as RIVALS
from tests.test_api_competitor_runs import offline, runner
from tests.test_api_runs_donors import FakeQueue
from tests.test_cli_main import BudgetRun, Serp, budget_run, logging_setups
from tests.test_judge_backfill import SiteSearch
from tests.test_model_no_credits import answered, answering, no_credits

#: Фикстуры соседних тестов: вход оператора и офлайн сметы конкурентов, консольный прогон.
__all__ = ["budget_run", "logging_setups", "offline", "runner"]

SITES = ("one-sample.com", "two-sample.com", "three-sample.com")
TEXTS = {host: SerpText(f"https://{host}/a", f"Обзоры {host}", "Статьи и обзоры") for host in SITES}
ACCEPTED = Judgement(Intent.EDITORIAL_ADS, Recommendation.ACCEPT, "Обзоры", "издание", "m", 400)
BROKE = Judgement(
    Intent.UNKNOWN, Recommendation.REVIEW, None, NO_CREDITS_WORDS, unanswered=True, no_credits=True
)
CLOSED = HomeSignals(reached=False, error="закрылась")
OPENED = HomeSignals(reached=True, title="Главная")


@pytest.fixture(autouse=True)
def _judge_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Судья площадки в наблюдении, главных не открывает сам: их отдаёт подделка теста."""
    monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.SHADOW)
    monkeypatch.setattr("backend.config.judge.HOME_CHECK", False)


def _index(monkeypatch: pytest.MonkeyPatch, asked: list[str]) -> None:
    """Индекс открывает каждую закрытую главную; арбитр — без кредитов, каждый вызов — в журнал."""

    async def homes(_index: object, blind: list[str]) -> tuple[dict[str, HomeSignals], float]:
        return dict.fromkeys(blind, OPENED), 0.003

    async def arbiter(_http: object, **kwargs: object) -> Judgement:
        asked.append(str(kwargs["host"]))
        return Judgement(
            Intent.UNKNOWN,
            Recommendation.REVIEW,
            None,
            NO_CREDITS_WORDS,
            decided_by=Decider.ARBITER,
            unanswered=True,
            no_credits=True,
        )

    monkeypatch.setattr("backend.features.donors.judging.index_homes", homes)
    monkeypatch.setattr("backend.features.donors.judging.arbitrate", arbiter)


# --- арбитр по индексу и сложение пачек ---------------------------------------------------------


async def test_the_index_arbiter_stops_on_the_first_no_credits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []
    _index(monkeypatch, asked)
    results: dict[str, tuple[Judgement, HomeSignals | None]] = dict.fromkeys(
        SITES, (ACCEPTED, CLOSED)
    )

    cost, stopped = await through_index(None, object(), results, TEXTS)  # type: ignore[arg-type]

    assert asked == [SITES[0]]
    assert (cost, stopped) == (0.003, NO_CREDITS_WORDS)
    # Вердикт по выдаче — у всех трёх, и у сайта, на котором арбитр встал: как при сбое индекса.
    assert [results[host][0] for host in SITES] == [ACCEPTED] * 3


async def test_a_pass_whose_index_arbiter_finds_no_credits_names_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []
    _index(monkeypatch, asked)

    async def judge(_http: object, **_kwargs: object) -> Judgement:
        return ACCEPTED

    async def closed(_client: object, _host: str) -> HomeSignals:
        return CLOSED

    monkeypatch.setattr("backend.features.donors.judging.judge_host", judge)
    monkeypatch.setattr("backend.features.donors.judging.check_home", closed)

    found = await judge_candidates(
        None,  # type: ignore[arg-type]
        list(SITES),
        TEXTS,
        home_client=object(),  # type: ignore[arg-type]
        index=object(),  # type: ignore[arg-type]
    )

    assert len(asked) == 1
    assert (found.summary.stopped, found.summary.unanswered_reason) == (
        NO_CREDITS_WORDS,
        NO_CREDITS_WORDS,
    )
    assert sorted(found.verdicts) == sorted(SITES)  # вердикты по выдаче легли у всех


@pytest.mark.parametrize("broke_first", [False, True], ids=["timeout-first", "no-credits-first"])
def test_no_credits_wins_the_reason_when_chunks_are_summed(broke_first: bool) -> None:
    timeout = JudgeSummary(unanswered=1, unanswered_reason="ReadTimeout('timed out')")
    broke = JudgeSummary(unanswered=2, unanswered_reason=NO_CREDITS_WORDS, stopped=NO_CREDITS_WORDS)
    summed = JudgeSummary()

    for part in (broke, timeout) if broke_first else (timeout, broke):
        summed.merge(part)

    assert (summed.unanswered, summed.unanswered_reason) == (3, NO_CREDITS_WORDS)
    assert summed.stopped == NO_CREDITS_WORDS


@pytest.mark.parametrize("broke_first", [False, True], ids=["timeout-first", "no-credits-first"])
def test_no_credits_wins_the_reason_of_the_topics(broke_first: bool) -> None:
    timeout = TopicVerdict(None, None, "ReadTimeout('timed out')", unanswered=True)
    broke = TopicVerdict(None, None, NO_CREDITS_WORDS, unanswered=True, no_credits=True)
    summary = TopicSummary()

    for verdict in (broke, timeout) if broke_first else (timeout, broke):
        summary.record(verdict)

    assert (summary.unanswered, summary.unanswered_reason) == (2, NO_CREDITS_WORDS)


# --- смета прогона по ссылкам конкурентов -------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "reply", "said"),
    [
        (JudgeMode.SHADOW, no_credits, NO_CREDITS_WORDS),
        (JudgeMode.SHADOW, answered, None),
        (JudgeMode.OFF, no_credits, None),
    ],
    ids=["no-credits", "answers", "judge-off"],
)
async def test_the_rivals_estimate_names_why_the_model_is_not_ready(
    client: AsyncClient,
    session: AsyncSession,
    runner: str,
    offline: FakeQueue,
    monkeypatch: pytest.MonkeyPatch,
    mode: JudgeMode,
    reply: Any,
    said: str | None,
) -> None:
    await known_key(session, key_limit=100_000)
    monkeypatch.setattr("backend.config.judge.MODE", mode)
    provider = answering(monkeypatch, reply)

    response = await client.post(
        "/api/runs/competitors/estimate", json=RIVALS, headers=bearer(runner)
    )

    assert response.status_code == 200, response.text
    assert response.json()["model_refusal"] == said
    assert provider.calls == (0 if mode is JudgeMode.OFF else 1)


# --- консольный прогон ---------------------------------------------------------------------------


def test_the_console_run_asks_the_model_before_buying_anything(
    budget_run: BudgetRun, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    answering(monkeypatch, no_credits)
    searched: list[tuple[str, ...]] = []

    class Watched(Serp):
        async def search(self, keywords: Any, country: str, *, depth_pages: int = 1) -> Any:
            searched.append(tuple(keywords))
            return await super().search(keywords, country, depth_pages=depth_pages)

    monkeypatch.setattr("backend.cli.main.build_provider", lambda _client: Watched())

    assert main(budget_run.argv) == 3

    assert f"Не запущено: {NO_CREDITS_WORDS}" in capsys.readouterr().err
    assert searched == []
    assert budget_run.ahrefs.paths == []
    assert budget_run.prompts == []


# --- досуд судьи ---------------------------------------------------------------------------------


async def test_the_judge_backfill_asks_the_model_before_buying_anything(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    answering(monkeypatch, no_credits)

    async def bought(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("досуд купил бы выдачу до проверки модели")

    monkeypatch.setattr(judge_backfill, "gather_candidates", bought)
    search = SiteSearch()
    plan = BackfillPlan(
        hosts=["homeonly.com"], saved={}, searches=[("us", ["old key"], 1)], skipped_reserved=0
    )

    with pytest.raises(ModelUnreadyError, match=NO_CREDITS_WORDS):
        await backfill(session, plan, http=None, home_client=object(), provider=search)  # type: ignore[arg-type]

    assert search.spent == 0


async def test_a_judge_backfill_without_credits_stops_the_later_chunks(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    for host in SITES:
        session.add(DomainModel(host=host))
    await session.commit()
    asked: list[str] = []

    async def judge(_http: object, **kwargs: object) -> Judgement:
        asked.append(str(kwargs["host"]))
        return BROKE

    monkeypatch.setattr("backend.features.donors.judging.judge_host", judge)
    monkeypatch.setattr(judge_backfill, "CHUNK", 1)
    plan = BackfillPlan(hosts=list(SITES), saved=dict(TEXTS), searches=[], skipped_reserved=0)

    report = await backfill(session, plan, http=None, home_client=None, provider=None)  # type: ignore[arg-type]

    assert asked == [SITES[0]]
    assert (report.judge.unanswered, report.judge.unanswered_reason) == (3, NO_CREDITS_WORDS)


# --- досуд тем -----------------------------------------------------------------------------------


def _topics() -> TopicPlan:
    texts = {host: SiteText(f"Статьи и обзоры {host}", "serp") for host in SITES}
    return TopicPlan(
        hosts=list(SITES), texts=texts, to_fetch=[], per_site=PerSite(100, 50, measured=False)
    )


def _topic_model(monkeypatch: pytest.MonkeyPatch, asked: list[str]) -> None:
    async def broke(_http: object, *, host: str, text: SiteText) -> TopicVerdict:
        asked.append(host)
        return TopicVerdict(None, None, NO_CREDITS_WORDS, unanswered=True, no_credits=True)

    monkeypatch.setattr("backend.features.topics.step.classify", broke)


async def test_the_topic_backfill_asks_the_model_first(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    answering(monkeypatch, no_credits)
    asked: list[str] = []
    _topic_model(monkeypatch, asked)

    with pytest.raises(ModelUnreadyError, match=NO_CREDITS_WORDS):
        await backfill_topics(session, _topics(), http=None, home_client=None)  # type: ignore[arg-type]

    assert asked == []


async def test_a_topic_backfill_without_credits_stops_the_later_chunks(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    _topic_model(monkeypatch, asked)
    monkeypatch.setattr(topic_backfill, "CHUNK", 1)

    report = await backfill_topics(session, _topics(), http=None, home_client=None)  # type: ignore[arg-type]

    assert asked == [SITES[0]]
    said = report.summary.as_dict()
    assert (said["unanswered"], said["unanswered_reason"]) == (3, NO_CREDITS_WORDS)
