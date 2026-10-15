"""Токены судьи сегмента — всего и раздельно: ввод, вывод, рассуждение, «раздельно неизвестно».

Смета прогона считает деньги модели долей вывода, заложенной предложением; у вывода своя цена,
и рассуждение модели идёт в счёт вывода. Раздельный счёт в отчёте прогона и пересуда даёт
настоящую долю. Строка журнала расхода — прежняя: итогом провайдера (`total_tokens`).

На настоящей базе: проход судьи, продолжение прогона (сумма по проходам), пересуд и его повтор;
разбор `usage` — формами ответа провайдера, в том числе без разбивки. Модель — подставным
транспортом, сайты и описание выдуманы.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from backend.config.judge import JudgeMode
from backend.features.core.domain import UsageProvider
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.sales import segment_rejudge as sr
from backend.features.sales.segment_judge import SegmentClient, Spent, spent_of
from backend.features.sales.segment_judging import judge_run
from backend.features.sales.segment_models import (
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from backend.features.sales.segment_rejudge_report import GOING, KEY, TOTALS, write_block
from backend.features.sales.segment_run import RunDeps, execute
from backend.features.sales.segment_window import SPLIT_TOKENS
from backend.features.sales.verifier import FixtureVerifier
from backend.features.serp.protocol import SerpResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_segment_judge import MODEL_KEY
from tests.test_sales_segment_rejudge import asked, report, site
from tests.test_sales_segment_run import KEYWORD, SEGMENT, Serp, hypothesis, make_run

Candidate = SalesSegmentCandidateModel

SHOP = "bikes-shop.example.test"
NEWS = "bikes-news.example.test"
PARTS = "bikes-parts.example.test"
#: Прогон целиком берёт из выдачи только регистрируемые домены — у зоны `.test` их нет.
SHOP_RUN, NEWS_RUN, PARTS_RUN = (
    "bikes-shop-sample.com",
    "bikes-news-sample.com",
    "bikes-parts-sample.com",
)
TITLES = {
    SHOP: "Горные велосипеды — интернет-магазин Велодом",
    NEWS: "Новости велоспорта и обзоры велосипедов",
    PARTS: "Запчасти для велосипедов — магазин Велодеталь",
}
TITLES |= {SHOP_RUN: TITLES[SHOP], NEWS_RUN: TITLES[NEWS], PARTS_RUN: TITLES[PARTS]}
YES = {"fit": "yes", "quote": "интернет-магазин Велодом", "why": "магазин велосипедов"}
#: Отказ магазину — чтобы прогон не дошёл до поиска адреса: принятых нет, сеть не нужна.
NO_SHOP = {"fit": "no", "quote": "интернет-магазин Велодом", "why": "не тот сегмент"}
NO_NEWS = {"fit": "no", "quote": "Новости велоспорта и обзоры", "why": "пишет о чужих"}
NO_PARTS = {"fit": "no", "quote": "Запчасти для велосипедов", "why": "запчасти, не велосипеды"}
#: Рассуждение съело потолок вывода: ответа нет, а вызов оплачен.
EMPTY = ""


def split(prompt: int, completion: int, reasoning: int = 0) -> dict[str, Any]:
    """`usage` ответа OpenAI: итог, ввод, вывод и сколько из вывода — рассуждение."""
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": reasoning},
    }


def whole(tokens: int) -> dict[str, Any]:
    """`usage` провайдера, который разбивки не дал: только итог."""
    return {"total_tokens": tokens}


@dataclass
class Billed:
    """Модель-подделка: ответ и `usage` по домену — очередью, по вызову; домен — из строки
    `Domain:` сообщения, где бы она ни стояла."""

    replies: dict[str, list[tuple[Any, dict[str, Any]]]]
    asked: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        user = json.loads(request.content)["messages"][1]["content"]
        host = next(
            line.removeprefix("Domain: ")
            for line in user.splitlines()
            if line.startswith("Domain:")
        )
        self.asked.append(host)
        body, usage = self.replies[host].pop(0)
        content = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        answer = {"choices": [{"message": {"content": content}}], "usage": usage}
        return httpx.Response(200, json=answer, request=request)


def judge(model: Billed) -> SegmentClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(model))
    return SegmentClient(http, model="gpt-5-mini", api_key=MODEL_KEY)


async def waiting(session: AsyncSession, run: SalesSegmentRunModel, *hosts: str) -> None:
    """Кандидаты прогона, как их заводит выдача: «ещё не судили», текст — заголовок страницы."""
    for host in hosts:
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()
        session.add(
            Candidate(
                run_id=run.id,
                hypothesis_id=run.hypothesis_id,
                domain_id=domain.id,
                page_title=TITLES[host],
                lane=Lane.PENDING,
            )
        )
    await session.commit()


async def judged(session: AsyncSession, run: SalesSegmentRunModel, model: Billed) -> dict[str, Any]:
    tally = await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(model),
        home=None,
    )
    return tally.as_dict()


def split_of(numbers: dict[str, Any]) -> tuple[int, ...]:
    """Токены всего и раздельно: всего, ввод, вывод, «раздельно неизвестно», рассуждение."""
    return (numbers["tokens"], *(numbers[key] for key in SPLIT_TOKENS))


# --- разбор `usage` ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("usage", "spent"),
    [
        (split(1000, 120, 64), Spent(1000, 120, 64)),
        ({"prompt_tokens": 900, "completion_tokens": 50, "total_tokens": 950}, Spent(900, 50, 0)),
        (whole(950), None),
        (None, None),
        ({"prompt_tokens": 900, "total_tokens": 900}, None),
        ({"prompt_tokens": 900, "completion_tokens": "50", "total_tokens": 950}, None),
        ({"prompt_tokens": -1, "completion_tokens": 951, "total_tokens": 950}, None),
        ({"prompt_tokens": True, "completion_tokens": 949, "total_tokens": 950}, None),
        # Части не сходятся с итогом, по которому пишется расход: раздельно неизвестно.
        ({"prompt_tokens": 900, "completion_tokens": 50, "total_tokens": 1000}, None),
        ({"prompt_tokens": 900, "completion_tokens": 50}, None),
    ],
    ids=[
        "openai",
        "no-reasoning-details",
        "total-only",
        "no-usage",
        "no-completion",
        "string",
        "negative",
        "bool",
        "parts-off-the-total",
        "no-total",
    ],
)
def test_the_usage_of_an_answer_splits_into_input_output_and_reasoning(
    usage: dict[str, Any] | None, spent: Spent | None
) -> None:
    body: dict[str, Any] = {"choices": []} if usage is None else {"choices": [], "usage": usage}

    assert spent_of(body) == spent


# --- проход судьи -----------------------------------------------------------------------------------


async def test_a_pass_counts_the_tokens_in_total_and_split(session: AsyncSession) -> None:
    """Ввод, вывод и рассуждение — суммой по вызовам; строка расхода — прежняя, итогом."""
    run = await make_run(session, await hypothesis(session))
    await waiting(session, run, SHOP, NEWS)
    model = Billed({SHOP: [(YES, split(1100, 300, 192))], NEWS: [(NO_NEWS, split(1050, 250, 128))]})

    found = await judged(session, run, model)

    assert split_of(found) == (2700, 2150, 550, 0, 320)
    lines = await session.scalars(
        select(UsageRecordModel.units).where(
            UsageRecordModel.operation == "sales_segment_judge",
            UsageRecordModel.provider == UsageProvider.LLM,
        )
    )
    assert list(lines.all()) == [2700]


async def test_tokens_of_a_provider_without_the_split_are_unsplit_and_not_zero(
    session: AsyncSession,
) -> None:
    """Провайдер не назвал ввод и вывод — его токены «раздельно неизвестно», а не ноль ни там,
    ни там: ввод, вывод и неизвестное в сумме — все токены прохода."""
    run = await make_run(session, await hypothesis(session))
    await waiting(session, run, SHOP, NEWS)
    model = Billed({SHOP: [(YES, whole(1400))], NEWS: [(NO_NEWS, split(1050, 250, 128))]})

    found = await judged(session, run, model)

    assert split_of(found) == (2700, 1050, 250, 1400, 128)


async def test_a_paid_empty_answer_counts_its_reasoning(session: AsyncSession) -> None:
    """Рассуждение съело потолок вывода: ответа нет, сайт ждёт следующего прохода, а токены
    оплачены — и все они вывод."""
    run = await make_run(session, await hypothesis(session))
    await waiting(session, run, SHOP)
    model = Billed({SHOP: [(EMPTY, split(1100, 2000, 2000))]})

    found = await judged(session, run, model)

    assert found["unanswered"] == 1
    assert split_of(found) == (3100, 1100, 2000, 0, 2000)


# --- прогон: сумма по проходам ------------------------------------------------------------------


async def go(session: AsyncSession, run: SalesSegmentRunModel, model: Billed) -> dict[str, Any]:
    """Проход прогона целиком. Выдача — три сайта; принятых нет — адреса и лиды не ищутся."""
    rows = [
        SerpResult(rank, f"https://{host}/", TITLES[host], None)
        for rank, host in enumerate((SHOP_RUN, NEWS_RUN, PARTS_RUN), start=1)
    ]
    deps = RunDeps(
        serp=Serp({KEYWORD: rows}), judge=judge(model), home=None, verifier=FixtureVerifier()
    )
    return await execute(session, run.id, deps)


async def test_the_run_report_sums_the_split_over_passes(session: AsyncSession) -> None:
    """«Продолжить прогон» судит, за кого модель не ответила: отчёт — сумма проходов, а не
    последний (тест прогона: продолжение затирало счёт первого прохода)."""
    run = await make_run(session, await hypothesis(session))
    model = Billed(
        {
            SHOP_RUN: [(EMPTY, split(1100, 2000, 2000)), (NO_SHOP, split(1000, 100, 0))],
            NEWS_RUN: [(NO_NEWS, split(1050, 250, 128))],
            PARTS_RUN: [(NO_PARTS, whole(1300))],
        }
    )

    first = await go(session, run, model)
    second = await go(session, run, model)

    assert split_of(first["judge"]) == (5700, 2150, 2250, 1300, 2128)
    assert split_of(second["judge"]) == (5700 + 1100, 2150 + 1000, 2250 + 100, 1300, 2128)
    assert model.asked.count(SHOP_RUN) == 2


async def test_a_run_judged_before_the_split_keeps_its_early_tokens_outside_it(
    session: AsyncSession,
) -> None:
    """Прогон судился до разбивки: его ранние токены есть только в `tokens`. Продолжение
    раскладывает свои, и раздельно неизвестное у такого отчёта — `tokens` без ввода и вывода."""
    run = await make_run(session, await hypothesis(session))
    run.report = {"judge": {"tokens": 5000, "judged": 4}}
    await session.commit()
    model = Billed(
        {
            SHOP_RUN: [(NO_SHOP, split(1000, 100, 0))],
            NEWS_RUN: [(NO_NEWS, split(1000, 100, 0))],
            PARTS_RUN: [(NO_PARTS, split(1000, 100, 0))],
        }
    )

    found = (await go(session, run, model))["judge"]

    assert split_of(found)[:4] == (5000 + 3300, 3000, 300, 0)
    assert found["tokens"] - found["prompt_tokens"] - found["completion_tokens"] == 5000


# --- пересуд ------------------------------------------------------------------------------------


def rejudge_deps(model: Billed) -> sr.RejudgeDeps:
    return sr.RejudgeDeps(judge(model), None)


@pytest.fixture
async def run(session: AsyncSession) -> SalesSegmentRunModel:
    return await make_run(session, await hypothesis(session), mode=JudgeMode.ENFORCE)


async def test_a_rejudge_counts_the_tokens_in_total_and_split(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    await site(session, run, SHOP, lane=Lane.ACCEPT)
    await site(session, run, PARTS, lane=Lane.ACCEPT)
    await asked(session, run)
    model = Billed({SHOP: [(YES, split(1100, 300, 192))], PARTS: [(NO_PARTS, whole(1200))]})

    outcome = await sr.rejudge(session, run.id, rejudge_deps(model))

    assert split_of(outcome) == (2600, 1100, 300, 1200, 192)
    assert split_of((await report(session, run))[KEY]) == (2600, 1100, 300, 1200, 192)


async def test_a_retried_rejudge_adds_the_split_of_its_first_attempt(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    """Задача упала посреди пересуда — повтор продолжает его: токены первой попытки, всего и
    раздельно, не теряются."""
    shop = await site(session, run, SHOP, lane=Lane.REJECT)
    parts = await site(session, run, PARTS, lane=Lane.ACCEPT)
    earlier = {
        "state": GOING,
        "sites": {str(shop.id): ["reject", "accept"], str(parts.id): ["accept", None]},
        "tokens": 1500,
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "unsplit_tokens": 300,
        "reasoning_tokens": 64,
    }
    await write_block(session, run.id, earlier)
    await session.commit()
    model = Billed({PARTS: [(NO_PARTS, split(1100, 300, 192))]})

    outcome = await sr.rejudge(session, run.id, rejudge_deps(model))

    assert model.asked == [PARTS]
    assert split_of(outcome) == (1500 + 1400, 1000 + 1100, 200 + 300, 300, 64 + 192)


async def test_the_spend_of_every_rejudge_of_the_run_is_summed_apart_from_the_last_one(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    """Блок пересуда — последний пересуд, следующий его заменяет; расход прогона — за все:
    сумма копится своим ключом — сколько пересудов и их токены, всего и раздельно."""
    await site(session, run, SHOP, lane=Lane.ACCEPT)
    await site(session, run, PARTS, lane=Lane.ACCEPT)
    await asked(session, run)
    first = Billed({SHOP: [(YES, split(1100, 300, 192))], PARTS: [(NO_PARTS, whole(1200))]})
    await sr.rejudge(session, run.id, rejudge_deps(first))
    await asked(session, run)  # как маршрут запуска: новый пересуд заменяет блок прошлого
    second = Billed({SHOP: [(YES, split(1000, 200, 100))], PARTS: [(NO_PARTS, split(900, 150))]})

    await sr.rejudge(session, run.id, rejudge_deps(second))

    found = await report(session, run)
    assert split_of(found[KEY]) == (2250, 1900, 350, 0, 100)
    assert found[TOTALS] == {
        "count": 2,
        "tokens": 2600 + 2250,
        "prompt_tokens": 1100 + 1900,
        "completion_tokens": 300 + 350,
        "unsplit_tokens": 1200,
        "reasoning_tokens": 192 + 100,
    }


async def test_a_retried_rejudge_adds_to_the_sum_only_what_it_spent_itself(
    session: AsyncSession, run: SalesSegmentRunModel
) -> None:
    """Повтор задачи продолжает тот же пересуд: в сумму идут токены его чекпоинтов, а не снова
    токены первой попытки, и пересуд не считается вторым."""
    shop = await site(session, run, SHOP, lane=Lane.REJECT)
    parts = await site(session, run, PARTS, lane=Lane.ACCEPT)
    numbers = {"tokens": 1500, "prompt_tokens": 1000, "completion_tokens": 200}
    numbers |= {"unsplit_tokens": 300, "reasoning_tokens": 64}
    sites = {str(shop.id): ["reject", "accept"], str(parts.id): ["accept", None]}
    await write_block(session, run.id, {"state": GOING, "sites": sites, **numbers})
    run.report = {**(await report(session, run)), TOTALS: {"count": 1, **numbers}}
    await session.commit()

    await sr.rejudge(session, run.id, rejudge_deps(Billed({PARTS: [(NO_PARTS, split(1100, 300))]})))

    assert (await report(session, run))[TOTALS] == {
        "count": 1,
        "tokens": 1500 + 1400,
        "prompt_tokens": 1000 + 1100,
        "completion_tokens": 200 + 300,
        "unsplit_tokens": 300,
        "reasoning_tokens": 64,
    }
