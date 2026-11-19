"""Поле гипотезы «вид сайта» (срез 5.3 «Плана работ»): правило таблицы судьи, правка поля и кнопка
«Качество сайтов».

Таблица — на фактах, без модели; домены выдуманы (`*-sample.*`). Пустое поле — таблица решает, как до
поля: правило «вид не выбран» не срабатывает ни разу. Выбран вид — чужой вид отклоняется, и свой сайт
компании тоже; выбор заменяет виды из описания. «Не видно» поле не отклоняет — решает человек.
Правка — тем же замком и журналом гипотезы, что поле «страны компаний»; маршрут кнопки — стык.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Awaitable, Callable

import pytest
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.sales import hypotheses, plan_actions, segment_facts, segment_scope
from backend.features.sales import segment_judge as sj
from backend.features.sales.hypothesis_countries import CountryPolicy, OtherCountries
from backend.features.sales.hypothesis_kinds import (
    ANY_KIND,
    KIND_WORDS,
    SITE_KINDS,
    SiteKindError,
    checked_kinds,
    kinds_of,
    kinds_words,
)
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.plan_actions import Quality
from backend.features.sales.plan_models import SalesPlanHypothesisModel
from backend.features.sales.segment_facts import SiteFacts
from backend.features.sales.segment_judging import policy_of_run
from backend.features.sales.segment_models import Decider, Lane, SalesSegmentRunModel
from backend.features.sales.segment_rules import RULES, Case, decider, first
from backend.features.sales.segment_scope import Scope
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_plan_waves import NOW, sheet

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
SELLER = "kinds-seller@ours.example.test"
ROWS = "/api/sales/plan-hypotheses"
HOST = "clinic-sample.com"
#: Сегмент без места: вид сайта — единственное, что решает.
OWN = Scope(frozenset(), frozenset({"own"}))
DIRECTORIES = Scope(frozenset(), frozenset({"own", "intermediary"}))
GREECE = Scope(frozenset({"GR"}), frozenset({"own"}))
WITHOUT = tuple(rule for rule in RULES if rule.name != "kind_not_chosen")


def facts(kind: str = "own", core: str = "yes") -> SiteFacts:
    return SiteFacts(kind, core, "три слова цитаты", "сайт по тексту")


def kinds(*named: str) -> CountryPolicy:
    return CountryPolicy(kinds=frozenset(named))


def lane_of(
    known: SiteFacts | None, policy: CountryPolicy, scope: Scope | None = OWN
) -> tuple[Lane, str]:
    verdict = sj.settle(host=HOST, policy=policy, scope=scope, said=sj.Said(known, "сбой"))
    return verdict.lane, verdict.rule or ""


# --- виды — те, что различает судья ------------------------------------------------------------------


def test_the_kinds_are_the_judges_own_and_unseen_is_not_a_choice() -> None:
    assert SITE_KINDS == segment_scope.KINDS
    assert {*SITE_KINDS, "unclear"} == set(segment_facts.KINDS)
    assert set(KIND_WORDS) == set(SITE_KINDS)


# --- таблица правил ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("known", "scope", "policy"),
    list(
        itertools.product(
            [facts(kind, core) for kind in segment_facts.KINDS for core in ("yes", "unsure")],
            [OWN, DIRECTORIES, GREECE, None],
            [CountryPolicy(), CountryPolicy(OtherCountries.ACCEPT, frozenset({"PE"}))],
        )
    ),
)
def test_an_empty_field_decides_as_before_the_field(
    known: SiteFacts, scope: Scope | None, policy: CountryPolicy
) -> None:
    case = Case(HOST, policy=policy, scope=scope, facts=known)

    assert first(case).name == first(case, WITHOUT).name != "kind_not_chosen"


@pytest.mark.parametrize(
    ("known", "policy", "scope", "lane", "rule"),
    [
        # Пусто — как до поля: посредника и издание описание не просит — «нет»; просит — подходят.
        (facts("intermediary"), CountryPolicy(), OWN, Lane.REJECT, "kind_unwanted"),
        (facts("media"), CountryPolicy(), OWN, Lane.REJECT, "kind_unwanted"),
        (facts("intermediary"), CountryPolicy(), DIRECTORIES, Lane.ACCEPT, "segment_anywhere"),
        (facts("own"), CountryPolicy(), OWN, Lane.ACCEPT, "segment_anywhere"),
        # Выбран свой сайт компании — чужой вид «нет», даже если описание его просит.
        (facts("intermediary"), kinds("own"), DIRECTORIES, Lane.REJECT, "kind_not_chosen"),
        (facts("media"), kinds("own"), OWN, Lane.REJECT, "kind_not_chosen"),
        (facts("other"), kinds("own"), OWN, Lane.REJECT, "kind_not_chosen"),
        (facts("own"), kinds("own"), OWN, Lane.ACCEPT, "segment_anywhere"),
        # Выбран каталог — свой сайт компании «нет», каталог проходит, хоть описание его не просит.
        (facts("own"), kinds("intermediary"), OWN, Lane.REJECT, "kind_not_chosen"),
        (facts("intermediary"), kinds("intermediary"), OWN, Lane.ACCEPT, "segment_anywhere"),
        (facts("media"), kinds("intermediary", "media"), GREECE, Lane.ACCEPT, "no_places"),
        # Выбор не трогает остальное: другое дело — «нет» прежним правилом.
        (facts("media", "no"), kinds("media"), OWN, Lane.REJECT, "other_business"),
        # «Не видно» — человеку при любом выборе.
        (facts("unclear", "unsure"), kinds("own"), OWN, Lane.REVIEW, "unclear"),
        (facts("unclear", "yes"), kinds("intermediary"), OWN, Lane.REVIEW, "unclear"),
        # Порядок прежних правил: сбой модели и разбора описания — человеку, как раньше.
        (None, kinds("own"), OWN, Lane.REVIEW, "model_failed"),
        (facts("media"), kinds("own"), None, Lane.REVIEW, "scope_failed"),
    ],
    ids=[
        "empty-intermediary", "empty-media", "empty-asked-directories", "empty-own",
        "own-chosen-intermediary", "own-chosen-media", "own-chosen-state-body", "own-chosen-own",
        "directory-chosen-own", "directory-chosen-directory", "two-chosen-media",
        "chosen-another-business", "unclear-unsure", "unclear-yes", "model-failed",
        "scope-failed",
    ],
)  # fmt: skip
def test_each_case_of_the_kind_rule(
    known: SiteFacts | None, policy: CountryPolicy, scope: Scope | None, lane: Lane, rule: str
) -> None:
    assert lane_of(known, policy, scope) == (lane, rule)


def test_the_kind_rule_says_what_the_site_is_and_what_is_chosen_and_is_the_models() -> None:
    verdict = sj.settle(
        host=HOST,
        policy=kinds("media", "own"),
        scope=OWN,
        said=sj.Said(facts("intermediary"), ""),
    )
    rule = first(Case(HOST, policy=kinds("own"), scope=OWN, facts=facts("other")))

    # Слова правила — первыми, за ними — что модель сказала о сайте.
    assert verdict.reason.startswith(
        "каталог, биржа, площадка — такой вид сайта в гипотезе не выбран "
        "(выбран: сайт самой компании; издание или блог)"
    )
    assert decider(rule) is Decider.MODEL


# --- правка поля и слова -----------------------------------------------------------------------------


def test_a_change_is_lowercase_in_the_order_of_the_choice_without_repeats() -> None:
    assert checked_kinds([" Media", "own", "OWN"]) == ["own", "media"]
    assert checked_kinds([]) == []
    with pytest.raises(SiteKindError, match="вида сайта «catalog» нет: нужен один из own"):
        checked_kinds(["own", "catalog"])


def test_a_kind_the_code_does_not_know_is_not_counted_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        found = kinds_of(["own", "forum", " "])

    assert found == frozenset({"own"})
    assert kinds_of(None) == frozenset()
    assert "неизвестный вид сайта" in caplog.text


def test_the_field_in_words_is_the_chosen_kinds_or_as_the_description_asks() -> None:
    assert kinds_words(["media", "own"]) == "сайт самой компании; издание или блог"
    assert kinds_words([]) == ANY_KIND


async def test_a_run_judges_by_the_kinds_of_its_hypothesis_now(session: AsyncSession) -> None:
    hypothesis = SalesHypothesisModel(name="Вид сайта — прогон", site_kinds=["media", "nonsense"])
    session.add(hypothesis)
    await session.flush()

    policy = await policy_of_run(session, SalesSegmentRunModel(hypothesis_id=hypothesis.id))

    assert policy.kinds == frozenset({"media"})


async def _journal(session: AsyncSession, hypothesis_id: int) -> list[dict[str, str]]:
    found = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.target == f"sales_hypothesis:{hypothesis_id}")
        .order_by(AuditLogModel.id)
    )
    return [dict(log.details or {}) for log in found.all()]


async def test_the_quality_button_sets_the_kinds_with_the_journal_of_the_hypothesis(
    session: AsyncSession,
) -> None:
    row = await sheet(session)
    change = Quality(site_kinds=["intermediary", "own"])

    await plan_actions.change_quality(session, row.id, change, author_id=None, now=NOW)
    await plan_actions.change_quality(session, row.id, change, author_id=None, now=NOW)
    await plan_actions.change_quality(
        session, row.id, Quality(site_kinds=[]), author_id=None, now=NOW
    )

    found = await session.get(SalesHypothesisModel, row.hypothesis_id)
    assert found is not None
    assert found.site_kinds == []
    said = await _journal(session, row.hypothesis_id)
    # Тот же выбор второй раз — не правка: записей две, а не три.
    assert [(entry["действие"], entry["было"], entry["стало"]) for entry in said] == [
        (hypotheses.KINDS, ANY_KIND, "сайт самой компании; каталог, биржа, площадка"),
        (hypotheses.KINDS, "сайт самой компании; каталог, биржа, площадка", ANY_KIND),
    ]


async def test_a_kind_off_the_choice_is_refused_in_words_and_nothing_changes(
    session: AsyncSession,
) -> None:
    row = await sheet(session)

    with pytest.raises(hypotheses.BadKindsError, match="вида сайта «shop» нет"):
        await plan_actions.change_quality(
            session, row.id, Quality(site_kinds=["shop"]), author_id=None, now=NOW
        )

    assert await _journal(session, row.hypothesis_id) == []


# --- маршрут кнопки ----------------------------------------------------------------------------------


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


@pytest.fixture
async def row(session: AsyncSession) -> SalesPlanHypothesisModel:
    found = await sheet(session)
    await session.commit()
    return found


async def test_the_route_takes_the_kinds_and_the_card_of_the_hypothesis_shows_them(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    row: SalesPlanHypothesisModel,
) -> None:
    changed = await client.patch(
        f"{ROWS}/{row.id}/quality", json={"site_kinds": ["media", "own"]}, headers=headers
    )
    cards = await client.get("/api/sales/hypotheses", headers=headers)

    assert changed.status_code == 200, changed.text
    assert changed.json()["id"] == row.id
    [card] = [item for item in cards.json()["rows"] if item["id"] == row.hypothesis_id]
    assert card["site_kinds"] == ["own", "media"]


@pytest.mark.parametrize(
    ("body", "words"),
    [
        ({"site_kinds": ["shop"]}, "вида сайта «shop» нет: нужен один из own, intermediary"),
        ({}, "нечего менять: дайте глубину, цель, страны компаний, вид сайта или потолок"),
    ],
)
async def test_the_route_refuses_in_words(
    client: AsyncClient,
    headers: dict[str, str],
    row: SalesPlanHypothesisModel,
    body: dict[str, list[str]],
    words: str,
) -> None:
    response = await client.patch(f"{ROWS}/{row.id}/quality", json=body, headers=headers)

    assert response.status_code == 400
    assert words in response.json()["detail"]
