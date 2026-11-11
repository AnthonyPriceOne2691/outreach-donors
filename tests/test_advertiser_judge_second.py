"""Второй круг судьи рекламодателей: «не понять» первой модели перепроверяет вторая.

Модели и сайты — подставные: тесты не ходят ни к провайдеру, ни в сеть. Проверяется то, ради чего
круг устроен так: вторую модель зовут только при «не понять» и только когда она задана; судит она тот
же текст той же подсказкой, своим усилием; её исход ложится в строку ПЕРВОЙ когорты и меняет совет
всем, кто его читает; тот же текст вторая модель дважды не судит; пересчёт таблицей исход сохраняет;
строка без него читается как раньше; план, смета и итог называют круг числами; потолок и пустой
счёт останавливают и его.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from typing import Any

import httpx
import pytest
from backend.cli import advertiser_judge as cli
from backend.config import llm as llm_cfg
from backend.features.core.models.advertiser import AdvertiserJudgementModel, CandidateModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.crawl import judge_advice, judge_estimate, judge_pass, judge_second
from backend.features.crawl.judge_facts import SECOND_KEYS, LinkFacts
from backend.features.crawl.judge_pass import (
    NO_CREDITS,
    PassReport,
    plan_pass,
    report_lines,
    run_pass,
)
from backend.features.crawl.judge_rules import RULES_VERSION, SECOND_RULE, Advice, advise
from backend.features.crawl.judge_second import SECOND_EFFORT, SECOND_ROOM, SecondPlan
from backend.features.crawl.judge_store import relabel
from backend.shared.numbers import NBSP
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_advertiser_judge_pass as pass_tests
from tests.test_advertiser_judge import MODEL_KEY, Model, answer, facts_answer
from tests.test_advertiser_judge_pass import (
    OUTSIDE,
    SAMPLE,
    Site,
    _candidates,
    _judgements,
    _pending,
    _spend,
)

FIRST, SECOND = "gpt-5-mini", "gpt-5"
Judgement = AdvertiserJudgementModel
#: Слова правила «домен — бизнес, ссылка его продвигает» и спора с ответом модели — как на экране.
PROMOTED = "домен — бизнес, ссылка его продвигает"
DISPUTE = f"правила: пишем ({PROMOTED}); модель: не пишем"


def unsure(tokens: int = 100) -> httpx.Response:
    """Ответ «таблица — пишем, модель — не пишем»: спор, совет — «не понять»."""
    return facts_answer(tokens, verdict="skip")


def sure(tokens: int = 300) -> httpx.Response:
    """Согласное «пишем»: таблица и ответ модели совпали."""
    return facts_answer(tokens)


class Two:
    """Подставные модели: первая и вторая отвечают каждая своё, запросы — каждой свои."""

    def __init__(self, first: Model, second: Model) -> None:
        self.first, self.second = first, second

    def __call__(self, request: httpx.Request) -> httpx.Response:
        asked = json.loads(request.content)["model"]
        return (self.second if asked == SECOND else self.first)(request)

    @property
    def calls(self) -> tuple[int, int]:
        return len(self.first.requests), len(self.second.requests)


@pytest.fixture(autouse=True)
def second_on(monkeypatch: pytest.MonkeyPatch, _no_second_judge_round: None) -> None:
    """Вторая модель задана: общая фикстура тестов её гасит."""
    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_SECOND_MODEL", SECOND)


@pytest.fixture(autouse=True)
def model_ready(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Проверка модели до траты — подставная: настоящая пошла бы к провайдеру."""
    asked: list[str] = []

    async def ready(*models: str) -> None:
        asked.extend(models)

    monkeypatch.setattr(judge_pass, "ensure_model", ready)
    return asked


async def _run(session: AsyncSession, models: Two, **plan: Any) -> PassReport:
    transport = httpx.MockTransport(models)
    async with Site().client() as http, httpx.AsyncClient(transport=transport) as model_http:
        client = judge_pass.JudgeClient(model_http, model=FIRST, api_key=MODEL_KEY)
        return await run_pass(
            session, await plan_pass(session, **plan), client=client, http=http, delay=0
        )


async def _rows(session: AsyncSession) -> dict[tuple[str, str], AdvertiserJudgementModel]:
    """Строки суда по домену и модели."""
    return {(row.target_root, row.model): row for row in await _judgements(session)}


def _plain(lines: list[str]) -> list[str]:
    """Строки сервера с обычными пробелами: неразрывные держат число при слове на экране."""
    return [line.replace(NBSP, " ") for line in lines]


def _body(request: httpx.Request) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(request.content)
    return body


# --- факты и совет -------------------------------------------------------------------------------


def test_facts_without_a_second_round_keep_their_old_shape() -> None:
    facts = LinkFacts(
        money_anchor=True, target_sells=True, link_quote="a b c", target_quote="d e f"
    )
    stored = facts.as_json()

    assert not set(SECOND_KEYS) & set(stored)
    assert LinkFacts.from_json(stored) == facts
    # Исход второго круга — туда и обратно; не строка (правили руками) — как без него.
    judged = dataclasses.replace(
        facts, second_advice="write", second_words=PROMOTED, second_model=SECOND
    )
    assert set(SECOND_KEYS) <= set(judged.as_json())
    assert LinkFacts.from_json(judged.as_json()) == judged
    assert LinkFacts.from_json(stored | {"second_advice": 1, "second_model": None}) == facts


def test_the_second_round_is_the_last_step_of_the_advice() -> None:
    disputed = LinkFacts(
        money_anchor=True,
        calls_to_buy=True,
        target_sells=True,
        link_quote="a b c",
        target_quote="d e f",
        verdict="skip",
    )
    assert (advise(disputed).advice, advise(disputed).rule) == (Advice.UNCLEAR, "disagree")

    decided = advise(
        dataclasses.replace(
            disputed, second_advice="write", second_words=PROMOTED, second_model=SECOND
        )
    )
    assert (decided.advice, decided.rule) == (Advice.WRITE, SECOND_RULE)
    assert (
        decided.words == f"второй круг (модель gpt-5): пишем — {PROMOTED}; первый круг: {DISPUTE}"
    )

    still = advise(
        dataclasses.replace(
            disputed, second_advice="unclear", second_words=DISPUTE, second_model=SECOND
        )
    )
    assert (still.advice, still.rule) == (Advice.UNCLEAR, "disagree")
    assert still.words == f"{DISPUTE}; второй круг (модель gpt-5): не понять — {DISPUTE}"


def test_the_first_round_that_decided_needs_no_second() -> None:
    """Первый круг решил сам — исход второго не нужен; слово не из трёх советов — не исход."""
    agreed = LinkFacts(
        money_anchor=True,
        calls_to_buy=True,
        target_sells=True,
        link_quote="a b c",
        target_quote="d e f",
        verdict="write",
        second_advice="skip",
        second_words="ни одного признака рекламы",
        second_model=SECOND,
    )
    assert (advise(agreed).advice, advise(agreed).rule) == (Advice.WRITE, "promoted")

    odd = dataclasses.replace(agreed, verdict="skip", second_advice="maybe")
    assert (advise(odd).advice, advise(odd).rule) == (Advice.UNCLEAR, "disagree")


# --- проход --------------------------------------------------------------------------------------


async def test_only_unclear_goes_to_the_second_model_with_the_same_text(
    session: AsyncSession, model_ready: list[str]
) -> None:
    await _candidates(session, *_pending(*SAMPLE[:2]))
    models = Two(Model(unsure(), facts_answer(100)), Model(sure()))

    report = await _run(session, models)

    assert models.calls == (2, 1)
    assert model_ready == [FIRST, SECOND]
    first, second = _body(models.first.requests[0]), _body(models.second.requests[0])
    # Тот же текст, та же подсказка и строгая форма; усилие рассуждения — своё.
    assert second["messages"] == first["messages"]
    assert second["response_format"] == first["response_format"]
    assert (first["reasoning_effort"], second["reasoning_effort"]) == ("low", SECOND_EFFORT)
    assert second["max_completion_tokens"] == SECOND_ROOM > first["max_completion_tokens"]
    assert report.advice == {"write": 2}
    assert (report.second.sent, dict(report.second.advice)) == (1, {"write": 1})


async def test_the_outcome_lands_in_the_first_row_and_changes_the_advice(
    session: AsyncSession,
) -> None:
    [candidate_id] = await _candidates(session, *_pending(SAMPLE[0]))
    models = Two(Model(unsure(100)), Model(sure(300)))

    report = await _run(session, models)

    rows = await _rows(session)
    first, second = rows[(SAMPLE[0], FIRST)], rows[(SAMPLE[0], SECOND)]
    assert (first.advice, first.rule, first.rules_version) == ("write", SECOND_RULE, RULES_VERSION)
    assert (first.facts["verdict"], first.facts["second_advice"]) == ("skip", "write")
    assert (first.facts["second_words"], first.facts["second_model"]) == (PROMOTED, SECOND)
    # Строка второй модели — обычная строка своей когорты: её факты, её совет, её цена.
    assert (second.advice, second.rule, second.tokens) == ("write", "promoted", 300)
    assert not set(SECOND_KEYS) & set(second.facts)
    assert (report.tokens, report.advice) == (400, {"write": 1})
    assert [row.units for row in await _spend(session)] == [100, 300]

    candidate = await session.get(CandidateModel, candidate_id)
    assert candidate is not None
    candidate.confirmed = True
    await session.flush()
    said = (await judge_advice.after_decision(session, [candidate]))[candidate_id]
    assert (said.advice, said.rule, said.model) == (Advice.WRITE, SECOND_RULE, FIRST)
    assert said.words == f"второй круг (модель gpt-5): пишем — {PROMOTED}; первый круг: {DISPUTE}"


async def test_unclear_twice_stays_with_the_human_and_is_not_asked_again(
    session: AsyncSession,
) -> None:
    await _candidates(session, *_pending(SAMPLE[0]))
    models = Two(Model(unsure()), Model(unsure(300)))

    report = await _run(session, models)

    assert (report.advice, dict(report.second.advice)) == ({"unclear": 1}, {"unclear": 1})
    first = (await _rows(session))[(SAMPLE[0], FIRST)]
    assert (first.advice, first.rule, first.facts["second_advice"]) == (
        "unclear",
        "disagree",
        "unclear",
    )
    facts = LinkFacts.from_json(first.facts)
    assert facts is not None
    assert advise(facts).words.endswith(f"; второй круг (модель gpt-5): не понять — {DISPUTE}")
    # Нынешняя вторая модель пару уже судила: ни план, ни повтор «заново» её не спрашивают.
    plan = await plan_pass(session)
    assert (plan.ids, plan.fresh, plan.second) == (
        [],
        1,
        SecondPlan(SECOND, 0, 0, 300, from_history=True),
    )
    await _run(session, models, again=True)
    assert models.calls == (1, 1)


async def test_the_same_text_is_not_paid_twice_by_the_second_model(session: AsyncSession) -> None:
    await _candidates(session, *_pending(SAMPLE[0]))
    models = Two(Model(unsure(100)), Model(sure(300)))
    await _run(session, models)
    # Исход до строки первой когорты не дошёл (проход оборвался между двумя записями).
    bare = {
        key: value
        for key, value in (await _rows(session))[(SAMPLE[0], FIRST)].facts.items()
        if key not in SECOND_KEYS
    }
    await session.execute(
        update(Judgement)
        .where(Judgement.model == FIRST)
        .values(facts=bare, advice="unclear", rule="disagree")
    )
    await session.commit()

    plan = await plan_pass(session)
    assert (len(plan.ids), plan.fresh, plan.second and plan.second.waiting) == (1, 0, 1)
    report = await _run(session, models)

    # Страница открыта заново, текст тот же: обе модели ответили из кэша, платить не за что.
    assert models.calls == (1, 1)
    assert (report.tokens, report.cached, report.second.cached) == (0, 1, 1)
    assert [row.units for row in await _spend(session)] == [100, 300]
    first = (await _rows(session))[(SAMPLE[0], FIRST)]
    assert (first.advice, first.rule, first.facts["second_model"]) == ("write", SECOND_RULE, SECOND)


async def test_a_changed_page_asks_the_first_model_again(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Переобход: пара ждала второго круга, а текст страницы сменился — судит снова первая."""
    await _candidates(session, *_pending(SAMPLE[0]))
    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_SECOND_MODEL", "")
    models = Two(Model(unsure(), facts_answer(100)), Model(sure()))
    await _run(session, models)
    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_SECOND_MODEL", SECOND)
    monkeypatch.setattr(pass_tests, "FILLER", "The page was rewritten from the first line. " * 9)

    report = await _run(session, models)

    assert models.calls == (2, 0)
    assert (report.advice, report.second.sent) == ({"write": 1}, 0)


async def test_the_term_of_the_first_row_stays_and_it_stays_the_last_row_of_the_pair(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Срок фактов — их: запись исхода его не сдвигает. Строка второй модели — не новее строки
    первой: «последняя строка пары» по всем когортам — по-прежнему строка первой когорты."""
    await _candidates(session, *_pending(SAMPLE[0]))
    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_SECOND_MODEL", "")
    models = Two(Model(unsure()), Model(sure()))
    await _run(session, models)
    judged_at = (await _rows(session))[(SAMPLE[0], FIRST)].updated_at
    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_SECOND_MODEL", SECOND)

    await _run(session, models)

    rows = await _rows(session)
    first, second = rows[(SAMPLE[0], FIRST)], rows[(SAMPLE[0], SECOND)]
    assert (first.advice, first.rule, first.updated_at) == ("write", SECOND_RULE, judged_at)
    assert second.updated_at < first.updated_at
    latest = max(rows.values(), key=lambda row: (row.updated_at, row.id))
    assert latest.model == FIRST


async def test_relabel_gives_the_same_advice_and_rule_as_the_second_round_wrote(
    session: AsyncSession,
) -> None:
    """Колонки строки первой когорты после второго круга — то же, что пересчёт даёт её фактам:
    и когда вторая модель решила, и когда тоже сказала «не понять»."""
    await _candidates(session, *_pending(*SAMPLE[:2]))
    await _run(session, Two(Model(unsure()), Model(sure(), unsure(300))))
    written = {
        target: (row.advice, row.rule, row.rules_version, row.updated_at)
        for (target, model), row in (await _rows(session)).items()
        if model == FIRST
    }
    assert {target: said[:3] for target, said in written.items()} == {
        SAMPLE[0]: ("write", SECOND_RULE, RULES_VERSION),
        SAMPLE[1]: ("unclear", "disagree", RULES_VERSION),
    }
    # Срок строк — явно прежний: без него правка версии сдвинула бы его сама.
    await session.execute(
        update(Judgement).values(
            rules_version="advertiser-rules-v0", updated_at=Judgement.updated_at
        )
    )
    await session.commit()

    changed = await relabel(session)
    await session.commit()

    assert changed == 4
    after = {
        target: (row.advice, row.rule, row.rules_version, row.updated_at)
        for (target, model), row in (await _rows(session)).items()
        if model == FIRST
    }
    assert after == written
    first = (await _rows(session))[(SAMPLE[0], FIRST)]
    assert first.facts["second_advice"] == "write"


async def test_no_second_model_or_the_same_one_means_no_second_round(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, model_ready: list[str]
) -> None:
    await _candidates(session, *_pending(*SAMPLE[:2]))
    models = Two(Model(unsure()), Model(sure()))

    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_SECOND_MODEL", "")
    assert (await plan_pass(session, limit=1)).second is None
    report = await _run(session, models, limit=1)
    # Та же модель вторым кругом не судит: тот же вопрос — тот же ответ из её же кэша.
    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_SECOND_MODEL", FIRST)
    assert (await plan_pass(session)).second is None
    await _run(session, models)

    assert models.calls == (2, 0)
    assert model_ready == [FIRST, FIRST]
    assert (report.advice, report.second.sent, report_lines(report)[-1]) == (
        {"unclear": 1},
        0,
        "советы: не понять 1",
    )


# --- план, смета и итог --------------------------------------------------------------------------


async def test_the_plan_and_the_estimate_name_the_second_round(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Прежний проход, без второй модели: два «не понять» и одно «пишем».
    await _candidates(session, *_pending(*SAMPLE[:3]))
    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_SECOND_MODEL", "")
    models = Two(Model(unsure(), unsure(), facts_answer(100)), Model(sure()))
    await _run(session, models)
    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_SECOND_MODEL", SECOND)
    await _candidates(session, *_pending(OUTSIDE[0]))

    plan = await plan_pass(session)

    # Двое ждут второго круга и один новый; из новых «не понять», по истории, — две трети.
    fallback = judge_second.FALLBACK_TOKENS
    assert (len(plan.ids), plan.fresh) == (3, 1)
    assert plan.second == SecondPlan(SECOND, 2, 1, fallback, from_history=False)
    assert plan.tokens == 1 * 100 + 3 * fallback

    found = await judge_estimate.estimate(session)
    words = "\n".join(_plain(found.lines))
    assert (found.cases, found.tokens) == (3, plan.tokens)
    assert "Первый круг: до 100 токенов, по 100 на случай" in words
    assert "Второй круг для «не понять» (модель gpt-5): пар 3" in words
    assert "из них среди новых случаев — около 1" in words
    assert "запасное число, истории ещё нет" in words
    assert f"Всего токенов: до {plan.tokens:,}".replace(",", " ") in words

    args = argparse.Namespace(
        sample=False, verdict=None, limit=None, per_donor=None, again=False, dry_run=True, yes=True
    )
    async with Site().client() as http:
        client = judge_pass.JudgeClient(http, model=FIRST, api_key=MODEL_KEY)
        await cli.run_advertisers_judge(session, args, client=client, http=http)
    assert (
        "в том числе — Второй круг для «не понять» (модель gpt-5): пар 3"
        in capsys.readouterr().out.replace(NBSP, " ")
    )


async def test_the_report_counts_the_second_round(session: AsyncSession) -> None:
    await _candidates(session, *_pending(*SAMPLE[:3]))
    models = Two(Model(unsure(), unsure(), facts_answer(100)), Model(sure(300), unsure(200)))

    report = await _run(session, models)

    assert report.advice == {"write": 2, "unclear": 1}
    assert (report.second.sent, report.second.tokens, report.tokens) == (2, 500, 800)
    assert dict(report.second.advice) == {"write": 1, "unclear": 1}
    assert (
        "второй круг (модель gpt-5): пар 2, решил — пишем 1, не пишем 0; "
        "оставил «не понять» 1; токенов 500"
    ) in _plain(report_lines(report))
    assert report.second.fields()["advice"] == {"write": 1, "unclear": 1}


# --- деньги --------------------------------------------------------------------------------------


async def test_a_cap_stops_the_second_round_too(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    async def cap(_session: AsyncSession) -> None:
        # Пачка, второй круг первой пары — можно; перед вторым вопросом второй модели — выбран.
        calls.append(1)
        if len(calls) > 2:
            raise LlmCapExceededError("дневной потолок судьи рекламодателей выбран")

    monkeypatch.setattr(judge_pass, "within_caps", cap)
    await _candidates(session, *_pending(*SAMPLE[:3]))
    models = Two(Model(unsure()), Model(sure()))

    report = await _run(session, models)

    assert models.calls == (2, 1)
    assert (report.done, report.left) == (2, 1)
    assert report.stopped == "дневной потолок судьи рекламодателей выбран"
    # Пара, до которой второй круг не дошёл, осталась «не понять» и ждёт следующего прохода.
    assert (report.advice, report.second.sent) == ({"write": 1, "unclear": 1}, 1)
    plan = await plan_pass(session)
    assert (len(plan.ids), plan.fresh, plan.second and plan.second.waiting) == (2, 1, 1)


async def test_no_credits_on_the_second_model_stop_the_pass(session: AsyncSession) -> None:
    await _candidates(session, *_pending(*SAMPLE[:2]))
    broke = httpx.Response(429, json={"error": {"type": "insufficient_quota", "message": "quota"}})
    models = Two(Model(unsure()), Model(broke))

    report = await _run(session, models)

    assert models.calls == (1, 1)
    assert (report.stopped, report.left, report.second.silent) == (NO_CREDITS, 1, 1)
    first = (await _rows(session))[(SAMPLE[0], FIRST)]
    assert (first.advice, "second_model" in first.facts) == ("unclear", False)


async def test_a_second_model_without_facts_leaves_the_pair_waiting(session: AsyncSession) -> None:
    """Ответ оплачен, а фактов нет (не та форма): исхода нет, расход записан, пара ждёт."""
    await _candidates(session, *_pending(SAMPLE[0]))
    models = Two(Model(unsure(100)), Model(answer("не JSON", tokens=50)))

    report = await _run(session, models)

    assert (report.advice, report.tokens, report.second.silent) == ({"unclear": 1}, 150, 1)
    assert [row.units for row in await _spend(session)] == [100, 50]
    assert "второй круг без ответа модели: 1 — спросит следующий проход" in report_lines(report)
    assert [row.model for row in await _judgements(session)] == [FIRST]
    plan = await plan_pass(session)
    assert (len(plan.ids), plan.second and plan.second.waiting) == (1, 1)
