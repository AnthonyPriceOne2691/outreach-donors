"""Совет судьи рекламодателей — человеку только после его решения.

До решения совета нет ни в очереди проверки, ни в файле спорных: совет рядом с нерешённой строкой
тянул бы решение за собой, а по решениям человека меряется и скоринг, и сам судья.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.features.core.domain import UserRole, Verdict
from backend.features.core.models.access import UserModel
from backend.features.core.models.advertiser import AdvertiserJudgementModel
from backend.features.crawl import disputed_export
from backend.features.crawl.judge_advice import after_decision
from backend.features.crawl.judge_client import PROMPT_VERSION
from backend.features.crawl.judge_rules import RULES_VERSION, Advice
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.stage2_rows import crawled, scored
from tests.test_advertiser_judge import LINK_QUOTE, facts_body

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
DONOR = "donor.example.test"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _judgement(target: str, *, context: str = "c", at: datetime = NOW, **facts: Any) -> Any:
    return AdvertiserJudgementModel(
        donor_host=DONOR,
        target_root=target,
        context_print=context * 64,
        prompt_version=PROMPT_VERSION,
        prompt_print="p" * 16,
        examples_print="",
        model="gpt-5-mini",
        rules_version=RULES_VERSION,
        facts=facts_body(**facts),
        advice="write",
        rule="promoted",
        seen="Абзац со ссылкой: …",
        tokens=100,
        retried=False,
        updated_at=at,
    )


async def test_the_queue_shows_the_advice_only_under_a_decision(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, session: AsyncSession
) -> None:
    run = await crawled(session, DONOR)
    decided = await scored(session, run, "decided.example.test", verdict=Verdict.PENDING)
    decided.confirmed, decided.decided_by = False, "оператор@site.com"
    await scored(session, run, "open.example.test", verdict=Verdict.PENDING)
    session.add_all([_judgement("decided.example.test"), _judgement("open.example.test")])
    await session.commit()
    await make_user("совет@site.com", role=UserRole.OPERATOR)
    token = await sign_in("совет@site.com")

    answer = await client.get(
        "/api/advertisers", params={"include_decided": "true"}, headers=bearer(token)
    )

    assert answer.status_code == 200
    cards = {card["target_root"]: card for card in answer.json()["rows"]}
    assert cards["open.example.test"]["judge"] is None
    said = cards["decided.example.test"]["judge"]
    assert (said["advice"], said["link_quote"], said["model"]) == (
        "write",
        LINK_QUOTE,
        "gpt-5-mini",
    )
    assert said["words"]


async def test_the_disputed_file_holds_no_advice(session: AsyncSession) -> None:
    run = await crawled(session, DONOR)
    await scored(session, run, "open.example.test", verdict=Verdict.PENDING)
    session.add(_judgement("open.example.test"))
    await session.flush()

    rows, _ = await disputed_export.disputed(session)
    content = disputed_export.to_csv(rows).decode("utf-8-sig")

    assert LINK_QUOTE not in content
    assert not any("суд" in column.lower() for column in disputed_export.COLUMNS)


async def test_the_latest_judgement_gives_the_advice_by_the_rules_in_force(
    session: AsyncSession,
) -> None:
    run = await crawled(session, DONOR)
    candidate = await scored(session, run, "seller.example.test", verdict=Verdict.PENDING)
    candidate.confirmed = True
    old = _judgement("seller.example.test", context="a", at=NOW - timedelta(days=3))
    new = _judgement(
        "seller.example.test",
        context="b",
        source_of_fact=True,
        money_anchor=False,
        calls_to_buy=False,
        verdict="skip",
    )
    # Совет строки записан старой таблицей — показывается совет нынешней по фактам.
    new.advice, new.rules_version = "write", "advertiser-rules-v0"
    session.add_all([old, new])
    await session.flush()

    said = (await after_decision(session, [candidate]))[candidate.id]

    assert (said.advice, said.rule) == (Advice.SKIP, "citation")
    assert said.judged_at == NOW
