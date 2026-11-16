"""Экономный судья сбора — тариф OpenAI Flex (`segment_tier`, решение владельца 16.11.2026).

- Тариф — в смете прогона (`judge_tier`): нет ключа — обычный, тело запроса прежнее до байта;
  незнакомый — постоянный отказ словами до первой траты.
- Flex — `service_tier: "flex"` в каждом вызове судьи (факты и разбор описания), срок 15 минут у
  клиента и у вызова целиком; 429 (кроме «нет кредитов») — повторы с нарастающей паузой и
  разбросом, ограниченные числом; не прошло — «модель не ответила» словами, сайт ждёт следующего
  прохода. У обычного тарифа повторов сверх общих нет.
- Сайт не дождался ни повторами, ни сроком — остальные сайты прохода модель не спрашивают; ответ
  сайта, начатого раньше, снимает это. Ответ, обслуженный другим тарифом, — предупреждение.
- Деньги судьи экономного прогона — долей прайса; пересуд — ценами сметы; токены — те же.

Модель — подставным транспортом (`httpx.MockTransport`), паузы — подменой `segment_tier._sleep`:
сеть и часы не трогаются. Домены и описание выдуманы.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
import pytest
from backend.config import llm as llm_cfg
from backend.features.core.models.ops import UsageRecordModel
from backend.features.sales import segment_client, segment_facts, segment_jobs, segment_tier
from backend.features.sales import segment_judge as sj
from backend.features.sales.segment_models import Lane
from backend.features.sales.segment_rejudge_report import TOTALS
from backend.features.sales.segment_run import RunDeps, execute
from backend.features.sales.segment_spend import FLEX_FACT, SpendRow, spend_rows
from backend.features.sales.segment_tier import JudgeTier, UnknownTierError
from backend.features.sales.verifier import FixtureVerifier
from backend.features.serp.protocol import SerpResult
from backend.shared import llm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.segment_facts_fakes import decide_any, facts, is_scope, reply
from tests.test_model_no_credits import NO_CREDITS_BODY
from tests.test_sales_segment_jobs import _quiet
from tests.test_sales_segment_judge import MODEL_KEY, SEGMENT, SHOP, text
from tests.test_sales_segment_model_spend import BLENDED, IN, JUDGE, OUT, PRICED
from tests.test_sales_segment_run import KEYWORD, Serp, hypothesis, make_run
from tests.test_sales_segment_spend import REPORT, run_of
from tests.test_sales_segment_tokens import (
    NEWS_RUN,
    NO_NEWS,
    NO_PARTS,
    NO_SHOP,
    PARTS_RUN,
    SHOP_RUN,
    TITLES,
    Billed,
    split,
)

#: Фикстура задачи прогона — отсюда её видит pytest.
__all__ = ["_quiet"]

#: 429 «нет мощности», как его описывает документация Flex; признак в теле — не публикуется,
#: код повторяет любой 429, кроме «нет кредитов».
BUSY_BODY = {"error": {"message": "Resource unavailable", "type": "server_error", "code": None}}
QUOTE = "Велосипеды и запчасти"


@pytest.fixture
def pauses(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Паузы повторов Flex — записью, без сна."""
    seen: list[float] = []

    async def asleep(seconds: float) -> None:
        seen.append(seconds)

    monkeypatch.setattr(segment_tier, "_sleep", asleep)
    return seen


class Model:
    """Подставная модель: первые `busy` запросов — 429 (тело `refused`), дальше — факты сайта;
    `served` — тариф, который ответ называет обслужившим (пусто — поля нет). Тела запросов — для
    проверки."""

    def __init__(
        self, busy: int = 0, refused: dict[str, Any] | None = None, served: str | None = None
    ) -> None:
        self.busy, self.refused, self.served = busy, refused or BUSY_BODY, served
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        if len(self.bodies) <= self.busy:
            return httpx.Response(429, json=self.refused, request=request)
        if is_scope(request):
            return reply(request, {"countries": [], "kinds": ["own"]}, tokens=40)
        answer = reply(request, facts("yes", QUOTE, "магазин велосипедов"), tokens=731)
        if self.served is None:
            return answer
        return httpx.Response(200, json=answer.json() | {"service_tier": self.served})

    def client(self, tier: JudgeTier) -> sj.SegmentClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self))
        return sj.SegmentClient(http, model="gpt-5-mini", api_key=MODEL_KEY, tier=tier)


async def judged(model: Model, tier: JudgeTier = JudgeTier.FLEX) -> sj.SegmentVerdict:
    client = model.client(tier)
    try:
        return await decide_any(client, host=SHOP, segment=SEGMENT, text=text())
    finally:
        await client.aclose()


def grows(pauses: list[float]) -> bool:
    """Каждая пауза — от `FLEX_PAUSE_S` × 2ⁿ до полутора её: нарастает, с разбросом."""
    return all(
        segment_tier.FLEX_PAUSE_S * 2**n <= pause <= 1.5 * segment_tier.FLEX_PAUSE_S * 2**n
        for n, pause in enumerate(pauses)
    )


# --- признак в смете --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("estimate", "tier"),
    [
        (None, JudgeTier.STANDARD),
        ({}, JudgeTier.STANDARD),
        ({"judge_tier": None}, JudgeTier.STANDARD),
        ({"judge_tier": ""}, JudgeTier.STANDARD),
        ({"judge_tier": "standard"}, JudgeTier.STANDARD),
        ({"judge_tier": "flex", "serp_mode": "live"}, JudgeTier.FLEX),
        # Выдача «стандарт» тариф судьи не включает: его выбирают ради цены выдачи.
        ({"serp_mode": "standard"}, JudgeTier.STANDARD),
    ],
)
def test_the_tier_comes_from_the_run_estimate_and_its_absence_is_standard(
    estimate: dict[str, Any] | None, tier: JudgeTier
) -> None:
    assert segment_tier.tier_of(estimate) is tier


@pytest.mark.parametrize("raw", ["turbo", "FLEX", 5, ["flex"]])
def test_an_unknown_tier_is_a_permanent_refusal_in_words(raw: object) -> None:
    with pytest.raises(UnknownTierError) as refused:
        segment_tier.tier_of({"judge_tier": raw})

    assert refused.value.permanent is True
    assert f"тариф судьи «{raw}» неизвестен" in str(refused.value)
    # Отчёт по такому прогону не падает и скидку не выдумывает.
    assert segment_tier.known_tier({"judge_tier": raw}) is JudgeTier.STANDARD


# --- тело запроса -----------------------------------------------------------------------------


def test_the_standard_body_is_the_body_it_always_was() -> None:
    """Обычный тариф — тело до правки: те же поля в том же порядке, без `service_tier`."""
    before = {
        "model": "gpt-5-mini",
        "messages": [
            {"role": "system", "content": sj.load_prompt()},
            {"role": "user", "content": "сайт"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {**segment_facts.SCHEMA, "strict": True},
        },
        "max_completion_tokens": sj.TOKENS_REASONING,
        "reasoning_effort": sj.REASONING_EFFORT,
    }

    for body in (
        sj.build_payload("gpt-5-mini", user="сайт"),
        sj.build_payload("gpt-5-mini", user="сайт", tier=JudgeTier.STANDARD),
    ):
        assert list(body.items()) == list(before.items())


def test_the_flex_body_differs_by_the_service_tier_only() -> None:
    standard = sj.build_payload("gpt-5-nano", user="сайт")
    flex = sj.build_payload("gpt-5-nano", user="сайт", tier=JudgeTier.FLEX)

    assert flex == {**standard, "service_tier": "flex"}


async def test_every_call_of_an_economy_judge_asks_flex_and_a_standard_one_never(
    pauses: list[float],
) -> None:
    """Факты сайта и разбор описания — оба вызова экономного судьи на Flex; у обычного поля нет."""
    for tier, field in ((JudgeTier.FLEX, {"flex"}), (JudgeTier.STANDARD, {None})):
        model = Model()
        client = model.client(tier)
        await client.scope(SEGMENT)
        said = await client.judge(host=SHOP, segment=SEGMENT, text=text())
        await client.aclose()

        assert said.facts is not None
        assert len(model.bodies) == 2
        assert {body.get("service_tier") for body in model.bodies} == field
    assert pauses == []


# --- повторы 429 ------------------------------------------------------------------------------


async def test_a_busy_flex_is_asked_again_after_a_pause_and_its_answer_counts(
    pauses: list[float],
) -> None:
    """Три 429 подряд — попытки общего `post_chat`; затем пауза Flex и ответ: вердикт и токены его."""
    model = Model(busy=llm.ATTEMPTS)

    verdict = await judged(model)

    assert (verdict.lane, verdict.quote, verdict.tokens) == (Lane.ACCEPT, QUOTE, 731)
    assert not verdict.unanswered
    assert len(model.bodies) == llm.ATTEMPTS + 1
    assert len(pauses) == 1
    assert grows(pauses)


async def test_flex_busy_past_the_limit_is_unanswered_in_words_and_waits_for_the_next_pass(
    pauses: list[float],
) -> None:
    """Мощности нет и после повторов: сайт «модель не ответила» словами — не вердикт, не кэш;
    запросов — попытки `post_chat` на каждый повтор, пауз — по числу повторов, и они растут."""
    model = Model(busy=10_000)

    verdict = await judged(model)

    assert (verdict.lane, verdict.unanswered, verdict.no_credits) == (Lane.REVIEW, True, False)
    assert verdict.reason.startswith("модель не ответила: ")
    assert (
        f"экономный тариф занят и после {segment_tier.FLEX_RETRIES} повторов с паузой — "
        "HTTP 429: Resource unavailable"
    ) in verdict.reason
    assert not verdict.proven
    assert verdict.tokens == 0  # 429 не оплачивается
    assert len(model.bodies) == (segment_tier.FLEX_RETRIES + 1) * llm.ATTEMPTS
    assert len(pauses) == segment_tier.FLEX_RETRIES
    assert grows(pauses)


async def test_no_credits_on_flex_is_not_retried_and_stops_the_pass(pauses: list[float]) -> None:
    """«Нет кредитов» — не мощность: ни повторов общего вызова, ни пауз Flex; проход встанет."""
    model = Model(busy=10_000, refused=NO_CREDITS_BODY)

    verdict = await judged(model)

    assert verdict.no_credits is True
    assert (len(model.bodies), pauses) == (1, [])


async def test_a_standard_judge_has_no_retries_beyond_the_shared_ones(pauses: list[float]) -> None:
    """Обычный тариф — как был: 429 повторяет только `post_chat`, слова отказа прежние."""
    model = Model(busy=10_000)

    verdict = await judged(model, JudgeTier.STANDARD)

    assert len(model.bodies) == llm.ATTEMPTS
    assert pauses == []
    assert verdict.reason == (
        "модель не ответила: модель, отказ (можно повторить): HTTP 429: Resource unavailable"
    )


async def test_an_answer_served_by_another_tier_is_a_warning_not_silence(
    pauses: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    """Просили Flex, а ответ обслужен другим тарифом (`service_tier` ответа): вердикт — как есть,
    расхождение денег — предупреждением в журнал. Ответ Flex и ответ без поля — молча."""
    with caplog.at_level(logging.WARNING, logger=segment_tier.logger.name):
        verdicts = [await judged(Model(served=served)) for served in ("default", "flex", None)]

    assert [verdict.lane for verdict in verdicts] == [Lane.ACCEPT] * 3
    [record] = [r for r in caplog.records if "обслужен другим" in r.getMessage()]
    assert record.__dict__["served"] == "default"
    assert pauses == []


# --- проход перестаёт ждать -------------------------------------------------------------------

#: Сайт, которого проход уже не спрашивал, — словами `HELD`.
HELD_REASON = f"модель не ответила: модель, отказ (можно повторить): {segment_tier.HELD}"


async def test_after_one_site_gave_up_the_pass_asks_the_model_no_more(pauses: list[float]) -> None:
    """Сайт не дождался мощности и после всех повторов — следующий сайт того же прохода модель не
    спрашивает: отказ сразу словами, без запросов и пауз. Новый проход — новый клиент: спрашивает."""
    model = Model(busy=10_000)
    client = model.client(JudgeTier.FLEX)

    first = await decide_any(client, host=SHOP, segment=SEGMENT, text=text())
    asked, waited = len(model.bodies), len(pauses)
    second = await decide_any(client, host="wheels.example.test", segment=SEGMENT, text=text())
    await client.aclose()

    assert (first.unanswered, second.unanswered) == (True, True)
    assert (len(model.bodies), len(pauses)) == (asked, waited)
    assert second.reason == HELD_REASON
    assert (await judged(Model())).lane is Lane.ACCEPT


async def test_an_answer_of_a_site_begun_earlier_opens_the_pass_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сайт, начатый до отказа, всё же получил ответ — мощность есть, проход снова спрашивает."""
    release = asyncio.Event()

    async def served(_http: object, *, payload: dict[str, Any], **_kw: object) -> Any:
        if payload["site"] == "slow":
            await release.wait()
            return {"choices": []}
        return llm.Refusal(llm.RefusalKind.REFUSED, "занят", permanent=False, status=429)

    monkeypatch.setattr(segment_tier, "_served", served)
    flex = segment_tier.FlexPass()
    async with httpx.AsyncClient() as http:
        slow = asyncio.create_task(
            flex.post(http, api_key=MODEL_KEY, payload={"site": "slow"}, topic="тест")
        )
        await asyncio.sleep(0)
        refused = await flex.post(http, api_key=MODEL_KEY, payload={"site": "busy"}, topic="тест")
        held = await flex.post(http, api_key=MODEL_KEY, payload={"site": "next"}, topic="тест")
        gave_up = flex.gave_up
        release.set()
        answered = await slow

    assert isinstance(refused, llm.Refusal)
    assert refused.status == 429
    assert isinstance(held, llm.Refusal)
    assert held.detail == segment_tier.HELD
    assert (gave_up, answered, flex.gave_up) == (True, {"choices": []}, False)


# --- сроки ------------------------------------------------------------------------------------


async def test_the_flex_client_waits_fifteen_minutes_and_the_standard_one_its_own_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Срок Flex — 15 минут, как в примерах OpenAI; обычный — `LLM_TIMEOUT_S` в момент сборки."""
    monkeypatch.setattr(llm_cfg, "TIMEOUT_S", 181.0)

    async with (
        segment_tier.judge_http(JudgeTier.FLEX) as flex,
        segment_tier.judge_http(JudgeTier.STANDARD) as standard,
    ):
        assert segment_tier.FLEX_TIMEOUT_S == 900.0
        assert flex.timeout.read == 900.0
        assert standard.timeout.read == 181.0


async def test_a_judge_without_a_given_client_makes_and_closes_its_own_with_the_tier_time() -> None:
    own = sj.SegmentClient(api_key=MODEL_KEY, tier=JudgeTier.FLEX)
    given = httpx.AsyncClient()
    borrowed = sj.SegmentClient(given, api_key=MODEL_KEY, tier=JudgeTier.FLEX)

    assert (own.tier, own._http.timeout.read) == (JudgeTier.FLEX, segment_tier.FLEX_TIMEOUT_S)
    assert sj.SegmentClient(api_key=MODEL_KEY).tier is JudgeTier.STANDARD
    await own.aclose()
    await borrowed.aclose()
    assert own._http.is_closed
    assert not given.is_closed  # чужой клиент закрывает тот, кто дал
    await given.aclose()


async def test_flex_that_does_not_answer_in_time_is_a_late_failure_not_a_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Срок вызова целиком: ответа нет — «модель не ответила» словами, окно не ждёт дальше; и
    следующий сайт прохода модель уже не спрашивает."""
    asked: list[str] = []

    async def silent(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        await asyncio.sleep(30)
        return reply(request, facts("yes", QUOTE))

    monkeypatch.setattr(segment_tier, "FLEX_TIMEOUT_S", 0.05)
    http = httpx.AsyncClient(transport=httpx.MockTransport(silent))
    client = sj.SegmentClient(http, model="gpt-5-mini", api_key=MODEL_KEY, tier=JudgeTier.FLEX)
    started = time.monotonic()

    verdict = await decide_any(client, host=SHOP, segment=SEGMENT, text=text())
    after = await decide_any(client, host="wheels.example.test", segment=SEGMENT, text=text())

    assert time.monotonic() - started < 5
    assert (verdict.lane, verdict.unanswered) == (Lane.REVIEW, True)
    assert "экономный тариф не ответил за" in verdict.reason
    assert (len(asked), after.reason) == (1, HELD_REASON)
    await client.aclose()


# --- задача прогона ---------------------------------------------------------------------------


def _estimate(monkeypatch: pytest.MonkeyPatch, estimate: dict[str, Any]) -> None:
    async def estimate_of(_factory: object, _run_id: int) -> dict[str, Any]:
        return estimate

    monkeypatch.setattr(segment_jobs, "_estimate_of", estimate_of)


async def test_an_economy_run_judges_on_flex_with_the_flex_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[RunDeps] = []

    async def execute(_session: AsyncSession, _run_id: int, deps: RunDeps) -> dict[str, Any]:
        seen.append(deps)
        return {}

    monkeypatch.setattr(segment_jobs, "execute", execute)
    _estimate(monkeypatch, {"judge_tier": "flex", "serp_mode": "standard"})

    await segment_jobs.run_once(7)

    judge = seen[0].judge
    assert isinstance(judge, segment_client.SegmentClient)
    assert (judge.tier, judge._http.timeout.read) == (JudgeTier.FLEX, segment_tier.FLEX_TIMEOUT_S)


async def test_an_unknown_tier_stops_the_run_in_words_before_the_serp_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped: list[tuple[int, str]] = []

    async def stop(_factory: object, run_id: int, words: str) -> None:
        stopped.append((run_id, words))

    def unbuilt(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("выдача собрана при незнакомом тарифе судьи")

    monkeypatch.setattr(segment_jobs, "_stop", stop)
    monkeypatch.setattr(segment_jobs, "build_provider", unbuilt)
    _estimate(monkeypatch, {"judge_tier": "turbo"})

    with pytest.raises(UnknownTierError):
        await segment_jobs.run_once(7)

    [(run_id, words)] = stopped
    assert run_id == 7
    assert words.startswith("тариф судьи «turbo» неизвестен")


# --- деньги -----------------------------------------------------------------------------------


def rows(estimate: dict[str, Any], report: dict[str, Any]) -> dict[str, SpendRow]:
    return {row.item: row for row in spend_rows(run_of(estimate=estimate, report=report))}


def test_the_judge_of_an_economy_run_costs_the_flex_share_and_the_rejudge_the_list_price() -> None:
    """Судья экономного прогона — половиной цен сметы, словами «экономный тариф»; пересуд — обычным
    тарифом по ценам сметы; деньги вместе — из них. Токены — те же."""
    totals = {"count": 1, "tokens": 4_850, "prompt_tokens": 3_000}
    totals |= {"completion_tokens": 650, "unsplit_tokens": 1_200, "reasoning_tokens": 292}
    report = {**REPORT, "judge": JUDGE, TOTALS: totals}

    flex = rows({**PRICED, "judge_tier": "flex"}, report)
    standard = rows(PRICED, report)

    share = segment_tier.FLEX_PRICE_SHARE
    usd = round((32_000 * IN + 7_000 * OUT + 2_000 * BLENDED) * share / 1_000_000, 4)
    assert (flex["judge"].fact_units, flex["judge"].fact_usd) == (41_000, usd)
    assert flex["judge"].fact.endswith(FLEX_FACT)
    assert flex["rejudge"] == standard["rejudge"]
    rejudge_usd = flex["rejudge"].fact_usd or 0.0
    assert flex["money"].fact_usd == round(0.0132 + usd + rejudge_usd, 4)
    # Обычный прогон — как был: прайс, без слов тарифа.
    assert standard["judge"].fact_usd == round(
        (32_000 * IN + 7_000 * OUT + 2_000 * BLENDED) / 1e6, 4
    )
    assert not standard["judge"].fact.endswith(FLEX_FACT)


async def test_an_economy_run_pays_the_same_tokens_at_the_flex_price(
    session: AsyncSession, pauses: list[float]
) -> None:
    """Прогон целиком на Flex: каждый вызов судьи — с `service_tier`, журнал расхода — те же
    токены (тариф их не меняет, дневные потолки считают их как есть), деньги судьи в отчёте —
    долей прайса. Всем сайтам отказ — адресов и лидов нет, сеть не нужна."""
    run = await make_run(session, await hypothesis(session))
    run.estimate = {**run.estimate, "judge_tier": "flex", "usd_per_1m_input": 0.25}
    run.estimate |= {"usd_per_1m_output": 2.0}
    await session.commit()
    replies = {SHOP_RUN: NO_SHOP, NEWS_RUN: NO_NEWS, PARTS_RUN: NO_PARTS}
    model = Billed({host: [(said, split(1000, 300, 200))] for host, said in replies.items()})
    bodies: list[dict[str, Any]] = []

    def recording(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return model(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(recording))
    found = [
        SerpResult(rank, f"https://{host}/", TITLES[host], None)
        for rank, host in enumerate(replies, start=1)
    ]
    judge = sj.SegmentClient(http, model="gpt-5-mini", api_key=MODEL_KEY, tier=JudgeTier.FLEX)
    deps = RunDeps(serp=Serp({KEYWORD: found}), judge=judge, home=None, verifier=FixtureVerifier())

    await execute(session, run.id, deps)
    await http.aclose()

    assert len(bodies) == 4  # разбор описания и три сайта
    assert {body.get("service_tier") for body in bodies} == {"flex"}
    journal = await session.scalars(
        select(UsageRecordModel.units).where(UsageRecordModel.operation == "sales_segment_judge")
    )
    assert sum(units or 0 for units in journal.all()) == 3 * 1300
    judged_row = {row.item: row for row in spend_rows(run)}["judge"]
    assert judged_row.fact_units == 3 * 1300
    assert judged_row.fact_usd == round(3 * (1000 * 0.25 + 300 * 2.0) * 0.5 / 1e6, 4)
    assert pauses == []
