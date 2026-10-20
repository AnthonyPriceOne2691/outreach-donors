"""Поле гипотезы «страны компаний» и разбор описания в проходе судьи v4 — на базе.

- Сбой разбора описания — не «мест нет»: сайты — «на проверку» словами, факты оплачены и лежат в кэше,
  а следующий проход разбирает описание снова и ставит полосы по фактам без вызова модели о сайтах.
- Правка поля гипотезы — пересуд без модели: факты того же описания — из кэша, полосы ставит таблица
  правил по новому полю, смета пересуда это показывает (0 токенов, разбор описания — в отчёте).
- API карточки: чтение и запись поля, проверка кодов словами, журнал «было → стало», коды стран.

Модель — подставным транспортом (`tests/segment_facts_fakes.py`), домены и описания выдуманы.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.config.judge import JudgeMode
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.sales import segment_rejudge as sr
from backend.features.sales import segment_rejudge_estimate as se
from backend.features.sales import segment_scope
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.segment_judging import JudgeTally, judge_run
from backend.features.sales.segment_models import (
    Lane,
    SalesSegmentRunModel,
    SalesSegmentVerdictModel,
)
from backend.features.sales.segment_rules import SCOPE_FAILED
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.segment_facts_fakes import facts
from tests.test_sales_segment_judging import candidates, judge
from tests.test_sales_segment_rejudge import asked, deps
from tests.test_sales_segment_run import PARTS, SHOP, TITLES, Model, hypothesis, make_run

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
SELLER = "seller@ours.example.test"

#: Сегмент со страной — Португалия (данные теста): разбор описания так и отвечает.
PORTUGAL = {"countries": ["PT"], "kinds": ["own"], "excludes_networks": False}
#: Магазин — компания из Чили: на сайте видна только чужая для сегмента география (цитаты — из
#: описания выдачи сайта теста: код сверяет, что слова есть в тексте).
CHILE_SHOP = facts(
    "yes",
    "интернет-магазин Велошоп",
    "магазин велосипедов в Чили",
    home="CL",
    home_quote=f"О сайте {SHOP}",
    served=[{"countries": ["CL"], "quote": f"О сайте {SHOP}"}],
)
PARTS_NO = facts("no", "Запчасти для велосипедов", "запчасти, не велосипеды")


async def _pass(session: AsyncSession, run: SalesSegmentRunModel, model: Model) -> JudgeTally:
    owner = await session.get(SalesHypothesisModel, run.hypothesis_id)
    assert owner is not None
    return await judge_run(
        session,
        run_id=run.id,
        segment=owner.description or "",
        mode=JudgeMode(run.mode),
        client=judge(model),
        home=None,
    )


async def test_a_failed_parse_is_review_in_words_and_the_next_pass_decides_from_the_cache(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    shop, parts = await candidates(session, run.id, owner.id, SHOP, PARTS)
    broken = Model({SHOP: CHILE_SHOP, PARTS: PARTS_NO}, scope=503)

    await _pass(session, run, broken)

    for site in (shop, parts):
        await session.refresh(site)
        assert (site.lane, site.unanswered) == (Lane.REVIEW, True)
        assert site.reason is not None
        assert site.reason.startswith(SCOPE_FAILED)
    rows = (await session.scalars(select(SalesSegmentVerdictModel))).all()
    assert {row.facts is not None for row in rows} == {True}  # факты оплачены и не потерялись
    await session.refresh(run)
    assert run.report[segment_scope.KEY]["failed"]
    assert run.report[segment_scope.KEY]["words"].startswith("описание сегмента не разобрано")

    again = Model({}, scope=PORTUGAL)
    await _pass(session, run, again)

    await session.refresh(shop)
    await session.refresh(parts)
    assert (again.asked, again.scoped) == ([], 1)  # о сайтах модель не спрошена
    assert (shop.lane, shop.unanswered) == (Lane.REVIEW, False)
    assert shop.reason is not None
    assert shop.reason.startswith("видна только чужая география — Чили")
    assert parts.lane == Lane.REJECT
    await session.refresh(run)
    assert run.report[segment_scope.KEY]["words"] == (
        "страна сегмента по описанию — Португалия; виды сайтов — сайты самих компаний"
    )


async def test_the_parse_of_the_same_description_is_not_asked_twice(session: AsyncSession) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    await candidates(session, run.id, owner.id, SHOP)
    model = Model({SHOP: CHILE_SHOP}, scope=PORTUGAL, scope_tokens=97)

    first = await _pass(session, run, model)
    await candidates(session, run.id, owner.id, PARTS)
    later = Model({PARTS: PARTS_NO}, scope=PORTUGAL)
    await _pass(session, run, later)

    assert (model.scoped, later.scoped, later.asked) == (1, 0, [PARTS])
    await session.refresh(run)
    assert run.report[segment_scope.KEY]["tokens"] == 97
    assert first.tokens == 97 + 613  # разбор описания и факты сайта — в расходе прохода


@pytest.mark.parametrize(
    ("other", "excluded", "lane", "words"),
    [
        ("accept", [], Lane.ACCEPT, "гипотеза берёт компании из других стран"),
        ("review", ["CL"], Lane.REJECT, "гипотеза не пишет компаниям оттуда"),
    ],
    ids=["other-countries-fit", "excluded-country"],
)
async def test_a_rejudge_after_the_field_changes_asks_no_model_and_moves_the_lane(
    session: AsyncSession, other: str, excluded: list[str], lane: Lane, words: str
) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    [shop] = await candidates(session, run.id, owner.id, SHOP)
    await _pass(session, run, Model({SHOP: CHILE_SHOP}, scope=PORTUGAL))
    await session.refresh(shop)
    assert shop.lane == Lane.REVIEW  # чужая география, поле по умолчанию — человеку
    owner.other_countries, owner.excluded_countries = other, excluded
    await session.commit()

    found = await se.estimate(session, run)
    assert (found.model, found.from_facts, found.scope_calls, found.tokens) == (0, 1, 0, 0)
    assert any(line.startswith("По фактам из кэша — 1") for line in found.lines)
    await asked(session, run)
    silent = Model({}, scope=PORTUGAL)
    await sr.rejudge(session, run.id, deps(silent))

    await session.refresh(shop)
    assert (silent.asked, silent.scoped) == ([], 0)
    assert shop.lane == lane
    assert shop.reason is not None
    assert words in shop.reason


# --- API карточки гипотезы ----------------------------------------------------------------------------

HYPOTHESES = "/api/sales/hypotheses"


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


async def _made(client: AsyncClient, headers: dict[str, str]) -> int:
    response = await client.post(
        HYPOTHESES, json={"name": "пекарни", "description": "Пекарни: пекут сами."}, headers=headers
    )
    assert response.status_code == 201, response.text
    assert (response.json()["other_countries"], response.json()["excluded_countries"]) == (
        "review",
        [],
    )
    return int(response.json()["id"])


async def test_the_field_is_written_checked_and_journaled(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    made = await _made(client, headers)

    written = await client.patch(
        f"{HYPOTHESES}/{made}",
        json={"other_countries": "accept", "excluded_countries": ["cl", "PE", "CL"]},
        headers=headers,
    )

    assert written.status_code == 200, written.text
    assert (written.json()["other_countries"], written.json()["excluded_countries"]) == (
        "accept",
        ["CL", "PE"],
    )
    listed = (await client.get(HYPOTHESES, headers=headers)).json()["rows"]
    assert [(r["other_countries"], r["excluded_countries"]) for r in listed] == [
        ("accept", ["CL", "PE"])
    ]
    entry = await session.scalar(
        select(AuditLogModel)
        .where(
            AuditLogModel.action == AuditAction.USER_UPDATED,
            AuditLogModel.target == f"sales_hypothesis:{made}",
        )
        .order_by(AuditLogModel.id.desc())
        .limit(1)
    )
    assert entry is not None
    assert entry.details is not None
    details: dict[str, Any] = dict(entry.details)
    assert details["было"] == "компании из других стран — на проверку; не пишем компаниям из: —"
    assert details["стало"] == "компании из других стран — подходят; не пишем компаниям из: CL, PE"


@pytest.mark.parametrize(
    ("body", "words"),
    [
        ({"excluded_countries": ["Chile"]}, "«Chile» — не код страны"),
        ({"excluded_countries": ["QQ"]}, "страны с кодом QQ нет в ISO 3166-1"),
        ({"other_countries": "maybe"}, "«компании из других стран» — 'maybe'"),
    ],
    ids=["a-name", "not-a-country", "not-a-position"],
)
async def test_a_bad_field_is_422_in_words(
    client: AsyncClient, headers: dict[str, str], body: dict[str, Any], words: str
) -> None:
    made = await _made(client, headers)

    refused = await client.patch(f"{HYPOTHESES}/{made}", json=body, headers=headers)

    assert refused.status_code == 422
    assert words in refused.json()["detail"]


async def test_the_country_codes_are_the_table_the_field_checks(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    codes = (await client.get("/api/sales/countries", headers=headers)).json()["codes"]

    assert len(codes) == len(set(codes)) >= 240
    assert {"CL", "PE", "PT", "JP", "GB"} <= set(codes)
    assert codes == sorted(codes)


def test_the_titles_of_the_test_sites_are_unchanged() -> None:
    """Цитаты фактов теста — из заголовков выдачи сайтов прогона: правка заголовков их сломала бы."""
    assert "интернет-магазин Велошоп" in TITLES[SHOP]
    assert "Запчасти для велосипедов" in TITLES[PARTS]
