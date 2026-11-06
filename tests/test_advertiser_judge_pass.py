"""Проход судьи рекламодателей в тени: план до траты, выборка первой, потолки, журнал расхода.

Модель — подставной транспорт, сайты — подставные страницы: тесты не ходят ни к провайдеру, ни в
сеть. Проверяется то, ради чего проход устроен так: решение человека он не трогает, каждый суд —
строка журнала расхода, повтор продолжает с места, модель спрошена до траты, нет кредитов или
выбран потолок — проход встаёт, к одному донору — по одной странице.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from collections import Counter

import httpx
import pytest
from backend.cli import advertiser_judge as cli
from backend.config import crawl as crawl_cfg
from backend.config import llm as llm_cfg
from backend.features.core.domain import CrawlOutcome, StopReason, UsageProvider, Verdict
from backend.features.core.models.advertiser import AdvertiserJudgementModel, CandidateModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.usage import LlmCapExceededError, record
from backend.features.crawl import judge_pass
from backend.features.crawl.judge_pass import (
    FALLBACK_TOKENS,
    JUDGE_OPERATION,
    NO_CREDITS,
    plan_pass,
    run_pass,
    within_caps,
)
from backend.features.crawl.judge_rules import RULES_VERSION
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.repository import save_crawl
from backend.features.crawl.sample import sampled
from backend.shared.llm import ModelUnreadyError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_advertiser_judge import HOME_HTML, Model, client_of, facts_answer, facts_body

DONOR = "kitchen-notes.example"
#: Домены случайной выборки (md5 домена < «33») — в её порядке; и вне выборки.
SAMPLE = ("shop4.example", "shop22.example", "shop9.example", "shop6.example")
OUTSIDE = ("shop0.example", "shop1.example", "shop2.example")
HTML = {"content-type": "text/html; charset=utf-8"}
FILLER = "We changed one thing in our kitchen this month and every cup tasted better. " * 6


def _article(target: str) -> str:
    return f"""<html lang="en"><head><title>Kitchen notes</title></head><body><article>
<h1>Five mornings with a better coffee routine</h1><p>{FILLER}</p>
<p>If you want the same result, <a href="https://{target}/sale">buy a burr grinder</a> from
Grinder Shop and use code MORNING for ten percent off your first order.</p><p>{FILLER}</p>
</article></body></html>"""


class Site:
    """Подставные сайты: статья донора со ссылкой на домен из её адреса и главная домена. Считает,
    сколько запросов к хосту летело разом."""

    def __init__(self) -> None:
        self.hits: list[str] = []
        self.flying: Counter[str] = Counter()
        self.peak: Counter[str] = Counter()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.hits.append(str(request.url))
        self.flying[host] += 1
        self.peak[host] = max(self.peak[host], self.flying[host])
        try:
            await asyncio.sleep(0.01)
            if host == DONOR:
                target = request.url.path.rsplit("/", 1)[-1]
                return httpx.Response(200, text=_article(target), headers=HTML)
            return httpx.Response(200, text=HOME_HTML, headers=HTML)
        finally:
            self.flying[host] -= 1

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


@pytest.fixture(autouse=True)
def model_ready(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Проверка модели до траты — подставная: настоящая пошла бы к провайдеру."""
    asked: list[str] = []

    async def ready(*models: str) -> None:
        asked.extend(models)

    monkeypatch.setattr(judge_pass, "ensure_model", ready)
    return asked


async def _candidates(
    session: AsyncSession,
    *specs: tuple[str, Verdict, int],
    page: bool = True,
    donor: str = DONOR,
) -> list[int]:
    report = CrawlReport(
        host=donor, outcome=CrawlOutcome.OK, stop_reason=StopReason.EXHAUSTED, pages=[], links=[]
    )
    run = await save_crawl(session, report)
    await session.flush()
    made = [
        CandidateModel(
            crawl_run_id=run.id,
            donor_host=donor,
            target_root=target,
            points=points,
            verdict=verdict,
            best_page_url=f"https://{donor}/post/{target}" if page else None,
            best_anchor="buy a burr grinder",
        )
        for target, verdict, points in specs
    ]
    session.add_all(made)
    await session.flush()
    return [candidate.id for candidate in made]


def _md5(host: str) -> str:
    return hashlib.md5(host.encode("utf-8"), usedforsecurity=False).hexdigest()


def _pending(*targets: str, points: int = 3) -> list[tuple[str, Verdict, int]]:
    return [(target, Verdict.PENDING, points) for target in targets]


async def _spend(session: AsyncSession) -> list[UsageRecordModel]:
    found = await session.scalars(
        select(UsageRecordModel).where(UsageRecordModel.operation == JUDGE_OPERATION)
    )
    return list(found.all())


async def _judgements(session: AsyncSession) -> list[AdvertiserJudgementModel]:
    return list((await session.scalars(select(AdvertiserJudgementModel))).all())


# --- план ------------------------------------------------------------------------------------------


async def test_the_plan_puts_the_sample_first_in_its_order(session: AsyncSession) -> None:
    ids = await _candidates(
        session,
        ("shop0.example", Verdict.PENDING, 2),
        ("shop1.example", Verdict.BOUGHT, 5),
        *_pending(*reversed(SAMPLE)),
        ("shop3.example", Verdict.SKIPPED, 1),
    )
    await _candidates(session, *_pending("shop2.example"), page=False)
    by_target = dict(
        zip(
            ["shop0.example", "shop1.example", *reversed(SAMPLE), "shop3.example"], ids, strict=True
        )
    )

    plan = await plan_pass(session)

    expected = [by_target[target] for target in (*SAMPLE, "shop1.example", "shop0.example")]
    assert plan.ids == expected
    assert (plan.sampled, plan.without_page, plan.fresh) == (4, 1, 0)
    assert plan.by_verdict == {"pending": 5, "bought": 1}
    assert (plan.per_case, plan.from_history) == (FALLBACK_TOKENS, False)
    assert plan.tokens == 6 * FALLBACK_TOKENS


async def test_the_plan_narrows_to_the_sample_a_verdict_and_a_limit(session: AsyncSession) -> None:
    await _candidates(session, *_pending(*SAMPLE, *OUTSIDE), ("shop5.example", Verdict.BOUGHT, 4))

    assert len((await plan_pass(session, sample_only=True)).ids) == len(SAMPLE)
    assert (await plan_pass(session, verdicts=(Verdict.BOUGHT,))).by_verdict == {"bought": 1}
    limited = await plan_pass(session, limit=2)
    assert (len(limited.ids), limited.sampled) == (2, 2)


async def test_the_set_by_donors_caps_each_donor_and_reaches_every_one(
    session: AsyncSession,
) -> None:
    # Один донор с десятком спорных не забирает набор: калибровка видит все площадки.
    big = await _candidates(session, *_pending(*SAMPLE, *OUTSIDE), donor="big-blog.example")
    small = await _candidates(session, *_pending("shop7.example"), donor="small-news.example")
    await _candidates(session, *_pending("shop8.example"), donor="no-page.example", page=False)

    plan = await plan_pass(session, per_donor=2)

    assert (plan.donors, len(plan.ids), plan.without_page) == (2, 3, 1)
    assert set(small) <= set(plan.ids)
    # Внутри донора — первые по md5 домена, а не по баллу: случай берётся жребием, как выборка.
    by_target = dict(zip((*SAMPLE, *OUTSIDE), big, strict=True))
    first_two = sorted(by_target, key=_md5)[:2]
    assert set(plan.ids) & set(big) == {by_target[target] for target in first_two}
    assert plan.sampled == sum(sampled(target) for target in (*first_two, "shop7.example"))


async def test_the_set_by_donors_counts_only_new_cases(session: AsyncSession) -> None:
    # Уже судимые свежим судом в потолок донора не идут: повтор набора берёт следующих.
    await _candidates(session, *_pending(*SAMPLE, *OUTSIDE))
    model = Model(facts_answer(tokens=120))
    async with Site().client() as http:
        first = await plan_pass(session, per_donor=3)
        await run_pass(session, first, client=client_of(model), http=http, delay=0)

    second = await plan_pass(session, per_donor=3)

    assert (second.fresh, len(second.ids)) == (3, 3)
    assert set(second.ids).isdisjoint(first.ids)


# --- проход ----------------------------------------------------------------------------------------


async def test_a_pass_writes_judgements_and_spend_and_leaves_decisions(
    session: AsyncSession, model_ready: list[str]
) -> None:
    [decided, open_] = await _candidates(session, *_pending(*SAMPLE[:2]))
    candidate = await session.get(CandidateModel, decided)
    assert candidate is not None
    candidate.confirmed = False
    model = Model(facts_answer(tokens=120))
    site = Site()

    async with site.client() as http:
        report = await run_pass(
            session, await plan_pass(session), client=client_of(model), http=http, delay=0
        )

    assert (report.done, report.outcomes["judged"], report.advice["write"]) == (2, 2, 2)
    assert report.tokens == 240
    assert model_ready == ["gpt-5-mini"]
    assert len(await _judgements(session)) == 2
    spend = await _spend(session)
    assert [(row.units, row.provider, row.stage) for row in spend] == [
        (120, UsageProvider.LLM, "advertisers")
    ] * 2
    # В тени: решение человека — как было, нерешённый остался нерешённым.
    for candidate_id, confirmed in ((decided, False), (open_, None)):
        row = await session.get(CandidateModel, candidate_id)
        assert row is not None
        await session.refresh(row)
        assert row.confirmed is confirmed


async def test_a_second_pass_continues_and_again_asks_no_model_for_the_same_text(
    session: AsyncSession,
) -> None:
    await _candidates(session, *_pending(*SAMPLE[:2]))
    model = Model(facts_answer(tokens=120))
    site = Site()
    async with site.client() as http:
        await run_pass(
            session, await plan_pass(session), client=client_of(model), http=http, delay=0
        )

        again = await plan_pass(session)
        assert (again.ids, again.fresh) == ([], 2)
        assert (again.per_case, again.from_history) == (120, True)

        report = await run_pass(
            session,
            await plan_pass(session, again=True),
            client=client_of(model),
            http=http,
            delay=0,
        )

    assert (report.cached, report.tokens) == (2, 0)
    assert len(model.requests) == 2
    assert len(await _spend(session)) == 2


async def test_an_unready_model_stops_the_pass_before_any_spend(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unready(*_models: str) -> None:
        raise ModelUnreadyError("у модели нет кредитов — пополните счёт OpenAI")

    monkeypatch.setattr(judge_pass, "ensure_model", unready)
    await _candidates(session, *_pending(*SAMPLE[:2]))
    model = Model(facts_answer())
    site = Site()

    async with site.client() as http:
        with pytest.raises(ModelUnreadyError):
            await run_pass(
                session, await plan_pass(session), client=client_of(model), http=http, delay=0
            )

    assert (model.requests, site.hits) == ([], [])


async def test_no_credits_stops_the_pass_and_the_next_chunks_ask_nobody(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(judge_pass, "CHUNK", 2)
    await _candidates(session, *_pending(*SAMPLE, *OUTSIDE[:1]))
    no_credits = httpx.Response(
        429, json={"error": {"type": "insufficient_quota", "message": "quota"}}
    )
    model = Model(no_credits)
    site = Site()

    async with site.client() as http:
        report = await run_pass(
            session, await plan_pass(session), client=client_of(model), http=http, delay=0
        )

    assert len(model.requests) == 1
    assert (report.stopped, report.left, report.outcomes["unanswered"]) == (NO_CREDITS, 4, 1)
    assert report.refusals == {"нет кредитов": 1}
    assert await _judgements(session) == []
    assert await _spend(session) == []


async def test_a_cap_stops_the_pass_before_the_next_chunk(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(judge_pass, "CHUNK", 2)
    calls: list[int] = []

    async def cap(_session: AsyncSession) -> None:
        calls.append(1)
        if len(calls) > 1:
            raise LlmCapExceededError("дневной потолок судьи рекламодателей выбран")

    monkeypatch.setattr(judge_pass, "within_caps", cap)
    await _candidates(session, *_pending(*SAMPLE, *OUTSIDE[:1]))
    model = Model(facts_answer())
    site = Site()

    async with site.client() as http:
        report = await run_pass(
            session, await plan_pass(session), client=client_of(model), http=http, delay=0
        )

    assert (report.done, report.left) == (2, 3)
    assert report.stopped == "дневной потолок судьи рекламодателей выбран"
    assert len(model.requests) == 2


async def test_one_donor_gets_one_page_at_a_time(session: AsyncSession) -> None:
    await _candidates(session, *_pending(*SAMPLE))
    site = Site()

    async with site.client() as http:
        report = await run_pass(
            session,
            await plan_pass(session),
            client=client_of(Model(facts_answer())),
            http=http,
            delay=0,
        )

    assert report.outcomes["judged"] == len(SAMPLE)
    assert site.peak[DONOR] == 1
    assert sum(1 for hit in site.hits if DONOR in hit) >= len(SAMPLE)


async def test_the_judge_has_its_own_daily_cap(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_cfg, "ADVERTISER_JUDGE_DAILY_TOKEN_CAP", 100)
    record(session, operation=JUDGE_OPERATION, units=150)
    await session.flush()

    with pytest.raises(LlmCapExceededError, match="судьи рекламодателей"):
        await within_caps(session)


# --- команда ---------------------------------------------------------------------------------------


def _args(**changes: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "sample": False,
        "verdict": None,
        "limit": None,
        "per_donor": None,
        "again": False,
        "dry_run": False,
        "yes": True,
    }
    return argparse.Namespace(**(values | changes))


async def test_the_command_prints_the_plan_and_a_dry_run_spends_nothing(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _candidates(session, *_pending(*SAMPLE[:2], *OUTSIDE[:1]))
    model = Model(facts_answer())
    site = Site()

    async with site.client() as http:
        code = await cli.run_advertisers_judge(
            session, _args(dry_run=True, sample=True), client=client_of(model), http=http
        )

    printed = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "к суду: 2 (pending 2), из них в выборке 2, доноров 1" in printed
    assert "запасное число, истории ещё нет" in printed
    assert "Это план" in printed
    assert (model.requests, site.hits) == ([], [])


async def test_the_command_runs_and_names_every_outcome(
    session: AsyncSession, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(crawl_cfg, "DELAY_SEC", 0.0)
    await _candidates(session, *_pending(*SAMPLE[:2]))
    model = Model(facts_answer(tokens=120))
    site = Site()

    async with site.client() as http:
        code = await cli.run_advertisers_judge(session, _args(), client=client_of(model), http=http)

    printed = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "Итог: случаев 2, токенов 240" in printed
    assert "рассужены: 2" in printed
    assert "советы: пишем 2" in printed


def _by_old_table(target: str, advice: str) -> AdvertiserJudgementModel:
    """Строка суда, чей совет поставила прежняя таблица; по фактам — «пишем»."""
    return AdvertiserJudgementModel(
        donor_host=DONOR,
        target_root=target,
        context_print="c" * 64,
        prompt_version="advertiser-judge-test",
        prompt_print="p" * 16,
        examples_print="",
        model="gpt-5-mini",
        rules_version="advertiser-rules-v0",
        facts=facts_body(),
        advice=advice,
        rule="verdict",
        seen="Абзац со ссылкой: …",
        tokens=100,
        retried=False,
    )


async def _advice_and_tables(session: AsyncSession) -> list[tuple[str, str]]:
    found = await session.execute(
        select(AdvertiserJudgementModel.advice, AdvertiserJudgementModel.rules_version).order_by(
            AdvertiserJudgementModel.target_root
        )
    )
    return [(advice, version) for advice, version in found.tuples()]


async def test_the_relabel_shows_the_moves_first_and_writes_without_tokens(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    session.add_all([_by_old_table("one.example", "skip"), _by_old_table("two.example", "write")])
    await session.commit()
    before = [("skip", "advertiser-rules-v0"), ("write", "advertiser-rules-v0")]

    code = await cli.run_relabel(session, _args(dry_run=True))

    printed = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert f"таблицей {RULES_VERSION}: строк 2" in printed
    assert "не пишем → пишем: 1" in printed
    assert "совет тот же: 1" in printed
    assert "Это план" in printed
    assert await _advice_and_tables(session) == before

    code = await cli.run_relabel(session, _args())

    assert code == cli.EXIT_OK
    assert "Пересчитано строк: 2, токенов: 0" in capsys.readouterr().out
    assert await _advice_and_tables(session) == [("write", RULES_VERSION)] * 2


async def test_the_relabel_has_nothing_to_do_on_rows_of_this_table(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    row = _by_old_table("one.example", "write")
    row.rules_version = RULES_VERSION
    session.add(row)
    await session.commit()

    code = await cli.run_relabel(session, _args())

    printed = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "строк 0" in printed
    assert "Пересчитано" not in printed


async def test_the_relabel_without_a_terminal_writes_nothing(
    session: AsyncSession, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_terminal(_question: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", no_terminal)
    session.add(_by_old_table("one.example", "skip"))
    await session.commit()

    code = await cli.run_relabel(session, _args(yes=False))

    assert code == cli.EXIT_DECLINED
    assert "Не пересчитывали" in capsys.readouterr().out
    assert await _advice_and_tables(session) == [("skip", "advertiser-rules-v0")]
