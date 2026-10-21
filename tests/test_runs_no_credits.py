"""Модель без кредитов у прогона доноров (слово владельца 20.10.2026): прогон не начинается и не
судит вхолостую.

- Смета называет, почему модель суда не готова, и кнопку не даёт; «Запустить» (по ключам и по
  ссылкам конкурентов) — 409 словами до строки прогона: ни счёта выдачи, ни Ahrefs, ни очереди.
  Модель ответила — прогон встаёт в очередь. Судья выключен — модель не спрашивается.
- Задача спрашивает модель сама — в начале каждого запуска, и возобновлённого после смерти воркера,
  до любой траты: ни списков конкурентов, ни выдачи, ни Ahrefs; отказ закрывает прогон словами.
- Кредиты кончились посреди суда — до конца прохода модель не зовётся ни судьёй, ни темой сайта:
  остальные домены — «модель не ответила» с причиной словами, прогон идёт дальше.

Провайдер модели — подставной (`tests/test_model_no_credits.py`), выдача и Ahrefs — подделки
тестов прогона; домены выдуманы.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from backend.config.judge import JudgeMode
from backend.config.topics import TopicMode
from backend.features.core.domain import RunStatus
from backend.features.core.models.run import RunModel
from backend.features.donors.judging import judge_candidates
from backend.features.donors.publisher_judge import Intent, Judgement, Recommendation
from backend.features.runs.competitors import Ask as RivalsAsk
from backend.features.runs.pipeline import RunRequest, execute_run
from backend.features.runs.planning import Candidates, SerpText
from backend.features.topics.classifier import SiteText, TopicVerdict
from backend.features.topics.step import classify_many
from backend.shared.llm import NO_CREDITS_WORDS
from backend.workers import run_job as jobs
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_ahrefs_cap import _Plenty, known_key
from tests.test_api_runs_donors import RIVALS_BODY, RUN_BODY, FakeQueue
from tests.test_execute_run import GOOD, T, TestTheJudgeStandsBeforeTheBill, _ahrefs, _deps
from tests.test_execute_run import _settings_id as settings_id
from tests.test_model_no_credits import KEY, REFUSALS, Provider, answered, answering, no_credits
from tests.test_run_job import _queued_run, pipeline
from tests.test_serp_funds import admin_token, operator_token

#: Фикстуры соседних тестов: входы, задача прогона с подменённым сбором.
__all__ = ["admin_token", "operator_token", "pipeline"]

KINDS = list(REFUSALS)
Titled = TestTheJudgeStandsBeforeTheBill.Titled
HOSTS = ("one.example.test", "two.example.test", "three.example.test")
#: Домены прогона целиком: зону `.test` «кому не пишем» режет до суда.
SITES = ("one-sample.com", "two-sample.com", "three-sample.com")


@pytest.fixture(autouse=True)
def _judge_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Судья площадки в наблюдении, главных не открывает; тематика выключена (`conftest`)."""
    monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.SHADOW)
    monkeypatch.setattr("backend.config.judge.HOME_CHECK", False)


class Untouched:
    """Ahrefs и счёт выдачи, которых запуск не должен спрашивать: каждый вызов — в журнал."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def client(self) -> _Plenty:
        self.calls.append("ahrefs")
        return _Plenty()

    async def balance(self, _ahrefs: object) -> float | None:
        self.calls.append("serp-balance")
        return None


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    monkeypatch.setattr("backend.config.ahrefs.API_KEY", "ключ-для-теста")
    monkeypatch.setattr("backend.config.serp.SANDBOX", False)
    fake = FakeQueue()
    monkeypatch.setattr("backend.api.runs.routes.runs_queue", lambda: fake)
    return fake


@pytest.fixture
def untouched(monkeypatch: pytest.MonkeyPatch) -> Untouched:
    found = Untouched()
    monkeypatch.setattr("backend.api.runs.routes.AhrefsClient", found.client)
    monkeypatch.setattr("backend.api.runs.competitors.AhrefsClient", found.client)
    monkeypatch.setattr("backend.features.serp.funds.provider_balance", found.balance)
    return found


async def runs_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count(RunModel.id))) or 0)


# --- «Запустить» --------------------------------------------------------------------------------


@pytest.mark.parametrize("route", ["/api/runs", "/api/runs/competitors"], ids=["keys", "rivals"])
@pytest.mark.parametrize("kind", KINDS)
async def test_start_is_refused_in_words_before_the_serp_account_ahrefs_and_the_queue(
    client: AsyncClient,
    session: AsyncSession,
    operator_token: str,
    queue: FakeQueue,
    untouched: Untouched,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    route: str,
) -> None:
    reply, words = REFUSALS[kind]
    answering(monkeypatch, reply)
    body = RUN_BODY if route == "/api/runs" else RIVALS_BODY

    response = await client.post(route, json=body, headers=bearer(operator_token))

    assert response.status_code == 409
    assert response.json()["detail"] == words
    assert untouched.calls == []
    assert queue.calls == []
    assert await runs_count(session) == 0


async def test_start_goes_when_the_model_answers(
    client: AsyncClient,
    session: AsyncSession,
    operator_token: str,
    queue: FakeQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = answering(monkeypatch, answered)

    await known_key(session)
    response = await client.post("/api/runs", json=RUN_BODY, headers=bearer(operator_token))

    assert response.status_code == 200, response.text
    assert len(queue.calls) == 1
    assert provider.calls == 1
    assert await runs_count(session) == 1


async def test_with_the_judge_off_the_model_is_not_asked(
    client: AsyncClient,
    session: AsyncSession,
    operator_token: str,
    queue: FakeQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.OFF)
    provider = answering(monkeypatch, no_credits)

    await known_key(session)
    response = await client.post("/api/runs", json=RUN_BODY, headers=bearer(operator_token))

    assert response.status_code == 200, response.text
    assert provider.calls == 0


async def test_the_topic_model_is_asked_too_when_it_is_another(
    client: AsyncClient,
    operator_token: str,
    queue: FakeQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.config.topics.MODE", TopicMode.ON)
    monkeypatch.setattr("backend.config.llm.JUDGE_MODEL", "gpt-5-mini")
    monkeypatch.setattr("backend.config.llm.TOPIC_MODEL", "gpt-5-nano")
    provider = answering(monkeypatch, answered, no_credits)

    response = await client.post("/api/runs", json=RUN_BODY, headers=bearer(operator_token))

    assert response.status_code == 409
    assert response.json()["detail"] == NO_CREDITS_WORDS
    assert [body["model"] for body in provider.bodies] == ["gpt-5-mini", "gpt-5-nano"]
    assert queue.calls == []


# --- смета --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "reply", "said"),
    [
        (JudgeMode.SHADOW, no_credits, NO_CREDITS_WORDS),
        (JudgeMode.SHADOW, answered, None),
        (JudgeMode.OFF, no_credits, None),
    ],
    ids=["no-credits", "answers", "judge-off"],
)
async def test_the_estimate_names_why_the_model_is_not_ready(
    client: AsyncClient,
    session: AsyncSession,
    admin_token: str,
    monkeypatch: pytest.MonkeyPatch,
    mode: JudgeMode,
    reply: Any,
    said: str | None,
) -> None:
    await known_key(session)
    monkeypatch.setattr("backend.api.runs.routes.AhrefsClient", _Plenty)
    monkeypatch.setattr("backend.config.judge.MODE", mode)
    provider = answering(monkeypatch, reply)

    response = await client.post("/api/runs/estimate", json=RUN_BODY, headers=bearer(admin_token))

    assert response.status_code == 200, response.text
    assert response.json()["model_refusal"] == said
    assert provider.calls == (0 if mode is JudgeMode.OFF else 1)


# --- задача ---------------------------------------------------------------------------------


@pytest.fixture
def lists(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Шаг списков конкурентов задачи — подменён: каждый его вызов — в журнал."""
    called: list[int] = []

    async def prepare(*_args: object, **_kwargs: object) -> Any:
        called.append(1)
        raise AssertionError("списки конкурентов покупались бы до проверки модели")

    monkeypatch.setattr(jobs, "prepare", prepare)
    monkeypatch.setattr(jobs, "check_collect", lambda: None)
    return called


@pytest.mark.parametrize("rivals", [False, True], ids=["keys", "rivals"])
@pytest.mark.parametrize("resumed", [False, True], ids=["first-start", "resumed"])
async def test_every_start_of_the_task_asks_the_model_before_any_spend(
    session: AsyncSession,
    pipeline: dict[str, Any],
    lists: list[int],
    monkeypatch: pytest.MonkeyPatch,
    resumed: bool,
    rivals: bool,
) -> None:
    """Воркер взял задачу позже нажатия — или продолжает её после смерти прежнего: модель
    спрашивается до списков конкурентов, выдачи и Ahrefs, отказ закрывает прогон словами."""
    answering(monkeypatch, no_credits)
    run = await _queued_run(session)
    if rivals:
        run.competitors = RivalsAsk(targets=("rival-sample.com",), depth=100).as_dict()
    if resumed:
        run.status = RunStatus.RUNNING
        # Выдача прошлой попытки сохранена: продолжение её не покупает — но и до неё не доходит.
        saved = Candidates(hosts=list(SITES), keywords=1, results=3, empty_keywords=[], dropped=0)
        run.candidates = saved.as_dict()
    await session.commit()

    result = await jobs._search(run.id)
    await session.refresh(run)

    assert run.status is RunStatus.STOPPED
    assert run.stats["причина"] == f"остановлен: {NO_CREDITS_WORDS}"
    assert result["refused"]
    assert lists == []
    assert "request" not in pipeline  # до сбора не дошло: ни выдачи, ни Ahrefs


async def test_the_task_asks_once_and_goes_when_the_model_answers(
    session: AsyncSession,
    pipeline: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "check_collect", lambda: None)
    provider = answering(monkeypatch, answered)
    run = await _queued_run(session)

    await jobs._search(run.id)

    assert "request" in pipeline
    assert provider.calls == 1


# --- посреди прохода ----------------------------------------------------------------------------


async def test_the_judge_pass_stops_calling_the_model_once_credits_run_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.config.judge.CONCURRENCY", 1)
    monkeypatch.setattr("backend.config.llm.API_KEY", KEY)
    provider = Provider(no_credits)
    texts = {
        host: SerpText(f"https://{host}/a", f"Обзоры {host}", "Статьи и обзоры") for host in HOSTS
    }

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        found = await judge_candidates(http, list(HOSTS), texts)

    assert provider.calls == 1
    assert found.verdicts == {}
    assert (found.summary.unanswered, found.summary.unanswered_reason) == (3, NO_CREDITS_WORDS)
    assert found.summary.stopped == NO_CREDITS_WORDS


async def test_after_the_stop_later_chunks_ask_neither_the_judge_nor_the_topic_and_the_run_goes_on(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("backend.features.runs.judge_stage.JUDGE_CHUNK", 1)
    asked: list[str] = []

    async def judge(http: object, **kwargs: object) -> Judgement:
        asked.append(str(kwargs["host"]))
        return Judgement(
            Intent.UNKNOWN,
            Recommendation.REVIEW,
            None,
            "нет кредитов",
            unanswered=True,
            no_credits=True,
        )

    told: list[str | None] = []

    class Topics:
        """Шаг темы: что ему сказал суд пачки. Сам он модель после остановки не зовёт
        (`test_a_topic_without_credits_stops_the_later_chunks_and_names_why`)."""

        summary = None
        stopped: str | None = None

        async def classify(self, *_args: object, stopped: str | None = None) -> None:
            told.append(stopped)

    monkeypatch.setattr("backend.features.donors.judging.judge_host", judge)
    monkeypatch.setattr("backend.features.runs.judge_stage._topic_step", lambda *_a: Topics())
    paid: list[str] = []
    serp = Titled([f"https://{host}/x" for host in SITES])

    report = await execute_run(
        await _deps(session, serp, _ahrefs(dict.fromkeys(SITES, GOOD), watch=paid)),
        RunRequest(
            keywords=["k"], country="us", thresholds=T, settings_id=await settings_id(session)
        ),
    )

    assert len(asked) == 1
    assert told == [NO_CREDITS_WORDS] * 3  # тема каждой пачки знает: суд встал без кредитов
    assert report.judge is not None
    assert (report.judge.unanswered, report.judge.unanswered_reason) == (3, NO_CREDITS_WORDS)
    assert set(paid) == set(SITES)  # суд — не та ступень, ради которой стоит терять выдачу


async def test_the_topic_batch_stops_calling_the_model_once_credits_run_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.config.topics.CONCURRENCY", 1)
    monkeypatch.setattr("backend.config.llm.API_KEY", KEY)
    provider = Provider(no_credits)
    texts = {host: SiteText(f"Статьи и обзоры {host}", "serp") for host in HOSTS}

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
        verdicts = await classify_many(http, texts)

    assert provider.calls == 1
    assert all(verdict.unanswered for verdict in verdicts.values())
    assert {verdict.reason for verdict in verdicts.values()} == {NO_CREDITS_WORDS}


#: Домены прогона темы: зону `.test` «кому не пишем» режет до суда (как в `test_topic_run.py`).
TOPIC_SITES = ("example.com", "example.org", "example.net")


async def test_a_topic_without_credits_stops_the_later_chunks_and_names_why(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Повторный прогон: вердикты судьи — из кэша, модель зовёт только тема. Её «нет кредитов» в
    первой пачке — до конца прохода модель не зовут ни тема, ни судья; сайтам следующих пачек —
    «не ответила» той же причиной, без вызова, и в отчёте прогона — причина, а не нули."""
    judged: list[str] = []

    async def judge(http: object, **kwargs: object) -> Judgement:
        judged.append(str(kwargs["host"]))
        return Judgement(
            Intent.EDITORIAL_ADS, Recommendation.ACCEPT, "Заголовок", "издание", "m", 400
        )

    monkeypatch.setattr("backend.features.donors.judging.judge_host", judge)
    serp = Titled([f"https://{host}/a" for host in TOPIC_SITES])
    ask = RunRequest(
        keywords=["k"], country="us", thresholds=T, settings_id=await settings_id(session)
    )
    await execute_run(await _deps(session, serp, _ahrefs(dict.fromkeys(TOPIC_SITES, GOOD))), ask)
    assert len(judged) == 3  # первый прогон: вердикты судьи легли в кэш

    asked: list[str] = []

    async def broke(http: object, *, host: str, text: SiteText) -> TopicVerdict:
        asked.append(host)
        return TopicVerdict(None, None, NO_CREDITS_WORDS, unanswered=True, no_credits=True)

    monkeypatch.setattr("backend.config.topics.MODE", TopicMode.ON)
    monkeypatch.setattr("backend.features.topics.step.classify", broke)
    monkeypatch.setattr("backend.features.runs.judge_stage.JUDGE_CHUNK", 1)
    again = RunRequest(
        keywords=["k"], country="us", thresholds=T, settings_id=await settings_id(session)
    )
    report = await execute_run(
        await _deps(session, serp, _ahrefs(dict.fromkeys(TOPIC_SITES, GOOD))), again
    )

    assert len(asked) == 1, "тема зовёт модель один раз на проход"
    assert len(judged) == 3, "судья — из кэша"
    assert report.topics is not None
    said = report.topics.as_dict()
    assert (said["unanswered"], said["unanswered_reason"]) == (3, NO_CREDITS_WORDS)
    assert said["classified"] == 0


async def test_a_topic_without_credits_stops_the_judge_of_the_later_chunks(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сайты новые: судья первой пачки ответил, а тема — «нет кредитов». Счёт у них один: суд
    следующих пачек модель тоже не зовёт, их домены — «модель не ответила» той же причиной."""
    judged: list[str] = []

    async def judge(http: object, **kwargs: object) -> Judgement:
        judged.append(str(kwargs["host"]))
        return Judgement(
            Intent.EDITORIAL_ADS, Recommendation.ACCEPT, "Заголовок", "издание", "m", 400
        )

    async def broke(http: object, *, host: str, text: SiteText) -> TopicVerdict:
        return TopicVerdict(None, None, NO_CREDITS_WORDS, unanswered=True, no_credits=True)

    monkeypatch.setattr("backend.features.donors.judging.judge_host", judge)
    monkeypatch.setattr("backend.config.topics.MODE", TopicMode.ON)
    monkeypatch.setattr("backend.features.topics.step.classify", broke)
    monkeypatch.setattr("backend.features.runs.judge_stage.JUDGE_CHUNK", 1)
    serp = Titled([f"https://{host}/a" for host in TOPIC_SITES])
    ask = RunRequest(
        keywords=["k"], country="us", thresholds=T, settings_id=await settings_id(session)
    )

    report = await execute_run(
        await _deps(session, serp, _ahrefs(dict.fromkeys(TOPIC_SITES, GOOD))), ask
    )

    assert len(judged) == 1
    assert report.judge is not None
    assert (report.judge.unanswered, report.judge.unanswered_reason) == (2, NO_CREDITS_WORDS)
