"""Вариант (b) судьи сегмента — полоса «судья отказал бы»: от судьи до плитки, лиды и журнал.

Решение владельца 20.10: судья остаётся в наблюдении; его отказы — отдельной вкладкой и плиткой
«Судья отказал бы», решать по ним человеку не обязательно; «На проверку» — только сомнительные; лида
по такому сайту без человека нет, «Принять» оттуда — можно, с журналом. Проверяется на настоящей
базе и настоящем проходе судьи с подделкой модели:

- отказ модели и языка сайта в наблюдении — своя полоса, в `enforce` — отсечён; «не понять» и
  правило — «на проверке»; ниши разные, слова судьи — латиницей, японским письмом без пробелов,
  арабским справа налево и кириллицей;
- путь прогона целиком: адрес на главной есть, а лида нет, пока человек не принял сайт; после
  «Принять» продолжение прогона заводит лида; в журнале — из какой полосы решили;
- плитка отчёта и вкладка списка сайтов — числом и строками;
- отмена решения возвращает сайт в «судья отказал бы» — и сайт, отказ которому дал язык сайта, тоже:
  как от языка и с его словами, а не отсечённым правилом;
- пересуд кладёт старый отказ «на проверке» («Отказ: …») в свою полосу и говорит «было → стало»;
- экран знает каждую полосу и каждого «кто решил» сервера.

Домены и тексты выдуманы (`*.example.test`, `*-sample.*`).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from backend.api.sales.segment_sites import SiteView
from backend.config.judge import JudgeMode
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.sales import segment_rejudge as sr
from backend.features.sales import segment_report
from backend.features.sales import segment_undo as su
from backend.features.sales.segment_judging import judge_run
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from backend.features.sales.segment_rejudge_report import Change, changes, summary
from backend.features.sales.segment_sites import SiteLane
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_segment_judge_v3 import _candidate, _judge
from tests.test_sales_segment_language import RU, UK
from tests.test_sales_segment_rejudge import NEWS as OLD_NEWS
from tests.test_sales_segment_rejudge import NO_NEWS, asked, deps, lead_of, report
from tests.test_sales_segment_rejudge import SHOP as OLD_SHOP
from tests.test_sales_segment_rejudge import site as old_site
from tests.test_sales_segment_run import (
    ANSWERS,
    MAYBE,
    NEWS,
    SHOP,
    Model,
    go,
    hypothesis,
    lanes,
    leads,
    make_run,
    serp,
    sites,
)
from tests.test_sales_segment_undo import (
    CLINIC,
    MAYBE_SCHOOL,
    PORTAL,
    WORKSHOPS,
    _network,
    decided,
    judged,
    undone,
)

__all__ = ["_network"]  # сеть прогона (DNS, платная ступень) — отсюда фикстуру видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
Candidate = SalesSegmentCandidateModel

SELLER = "seller@ours.example.test"
RUNS = "/api/sales/segment/runs"
CANDIDATES = "/api/sales/segment/candidates"
FRONT = Path(__file__).resolve().parent.parent / "frontend/src/sales"
UK_HOST = "velo-maisternia-sample.com"
RU_HOST = "velo-masterskaya-sample.com"
RU_ANSWER = {"kind": "own", "fit": "yes", "quote": RU[:60].rsplit(" ", 1)[0], "why": "мастерская"}
LANGUAGE_WORDS = "язык сайта — украинский, письма — русский"


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> httpx.MockTransport:
    """Главные для лестницы адресов: адрес есть и у магазина, и у новостного сайта — лида у второго
    нет только потому, что судья ему отказал, а человек не принял."""
    found = sites({SHOP: f"hello@{SHOP}", NEWS: f"editor@{NEWS}"})
    transport = httpx.MockTransport(found)
    monkeypatch.setattr(
        "backend.features.contacts.search.guarded_client",
        lambda **_kw: httpx.AsyncClient(transport=transport),
    )
    return transport


async def _journal(session: AsyncSession, candidate_id: int) -> list[dict[str, Any]]:
    found = await session.scalars(
        select(AuditLogModel)
        .where(
            AuditLogModel.action == AuditAction.SITE_REVIEWED,
            AuditLogModel.target == f"sales_segment_candidate:{candidate_id}",
        )
        .order_by(AuditLogModel.id)
    )
    return [entry.details for entry in found]


async def _by_host(session: AsyncSession, run: SalesSegmentRunModel, host: str) -> Candidate:
    found = await session.scalar(
        select(Candidate)
        .join(DomainModel, DomainModel.id == Candidate.domain_id)
        .where(Candidate.run_id == run.id, DomainModel.host == host)
        .execution_options(populate_existing=True)
    )
    assert found is not None, host
    return found


# --- куда ложится отказ -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "refused"),
    [(JudgeMode.SHADOW, Lane.WOULD_REJECT), (JudgeMode.ENFORCE, Lane.REJECT)],
    ids=["shadow", "enforce"],
)
async def test_a_refusal_in_any_script_waits_in_its_own_lane_in_shadow_and_is_cut_in_enforce(
    session: AsyncSession, mode: JudgeMode, refused: Lane
) -> None:
    """Справочник мастерских (немецкий), портал школ (японский без пробелов) — отказ; школа или блог
    (арабский) — «не понять»; ветклиника (польский) — «да». «На проверку» — только сомнительные."""
    run, made = await judged(session, WORKSHOPS, PORTAL, MAYBE_SCHOOL, CLINIC, mode=mode)

    found = {host: (site.lane, site.verdict) for host, site in made.items()}
    assert found == {
        WORKSHOPS.host: (refused, Lane.REJECT),
        PORTAL.host: (refused, Lane.REJECT),
        MAYBE_SCHOOL.host: (Lane.REVIEW, Lane.REVIEW),
        CLINIC.host: (Lane.ACCEPT, Lane.ACCEPT),
    }
    # Слова судьи — как есть: что это отказ, говорит полоса, а не приставка к причине.
    assert made[PORTAL.host].reason == "英会話教室の比較ポータルで、自社のレッスンはない"
    view = await segment_report.run_view(session, run.id)
    assert view is not None
    counts = segment_report.summary(view)
    assert (counts["review"], counts["would_reject"], counts["rejected"]) == (
        1,
        2 if mode is JudgeMode.SHADOW else 0,
        0 if mode is JudgeMode.SHADOW else 2,
    )


# --- путь прогона целиком: лид — только после «Принять» человека ------------------------------------


async def test_a_refused_site_makes_no_lead_until_a_human_accepts_it_and_the_journal_says_from_where(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    web: httpx.MockTransport,
) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner, mode=JudgeMode.SHADOW)
    found = serp(SHOP, NEWS, MAYBE)

    await go(session, run, found, Model(ANSWERS))

    assert await lanes(session, run) == {
        SHOP: Lane.ACCEPT,
        NEWS: Lane.WOULD_REJECT,
        MAYBE: Lane.REVIEW,
    }
    assert [lead.email for lead in await leads(session)] == [f"hello@{SHOP}"]
    # Плитка отчёта и вкладка списка сайтов.
    shown = await client.get(f"{RUNS}/{run.id}", headers=headers)
    tab = await client.get(f"{RUNS}/{run.id}/sites?lane=would_reject", headers=headers)
    assert shown.status_code == 200, shown.text
    summary_of = shown.json()["summary"]
    assert (summary_of["review"], summary_of["would_reject"], summary_of["accepted"]) == (1, 1, 1)
    assert tab.status_code == 200, tab.text
    [row] = tab.json()["rows"]
    assert (row["host"], row["lane"], row["verdict"], row["reason"]) == (
        NEWS,
        "would_reject",
        "reject",
        "пишет о чужих, сам не продаёт",
    )
    assert tab.json()["lanes"]["would_reject"] == 1
    news = await _by_host(session, run, NEWS)
    maybe = await _by_host(session, run, MAYBE)

    accepted = await client.post(
        f"{CANDIDATES}/{news.id}/decision", json={"accept": True}, headers=headers
    )
    kept = await client.post(
        f"{CANDIDATES}/{maybe.id}/decision", json={"accept": False}, headers=headers
    )
    await go(session, run, found, Model(ANSWERS))

    assert (accepted.status_code, kept.status_code) == (200, 200)
    assert sorted(lead.email for lead in await leads(session)) == sorted(
        [f"hello@{SHOP}", f"editor@{NEWS}"]
    )
    [decision] = await _journal(session, news.id)
    assert (decision["решение"], decision["было"], decision["судья сегмента"]) == (
        "accepted",
        "судья отказал бы",
        "отсечён",
    )
    [other] = await _journal(session, maybe.id)
    assert (other["решение"], other["было"]) == ("rejected", "на проверке")


async def test_a_human_may_decide_a_refused_site_but_not_a_cut_one(
    session: AsyncSession,
) -> None:
    """Полосы, где решение за человеком, — одно место у решения и у отмены (`AWAITS_HUMAN`)."""
    _, made = await judged(session, WORKSHOPS)
    site = made[WORKSHOPS.host]
    assert site.lane == Lane.WOULD_REJECT

    decision = await segment_report.decide(session, site.id, accept=False, author=SELLER)

    assert (decision.was, decision.repeated, site.lane) == (Lane.WOULD_REJECT, False, Lane.REJECT)
    with pytest.raises(segment_report.DecisionRefusedError, match="он уже «отсечён»"):
        await segment_report.decide(session, site.id, accept=True, author=SELLER)


# --- отмена: туда, где сайт был -------------------------------------------------------------------


@pytest.mark.parametrize("accept", [True, False], ids=["accept", "reject"])
async def test_an_undo_puts_a_refused_site_back_into_its_own_lane_with_the_judges_words(
    session: AsyncSession, accept: bool
) -> None:
    _, made = await judged(session, PORTAL)
    site = made[PORTAL.host]
    await decided(session, site, accept=accept)

    found = await undone(session, site)

    await session.refresh(site)
    assert found.was == (Lane.ACCEPT if accept else Lane.REJECT)
    assert (site.lane, site.reason, site.decided_by) == (
        Lane.WOULD_REJECT,
        "英会話教室の比較ポータルで、自社のレッスンはない",
        Decider.MODEL,
    )


async def _language_run(
    session: AsyncSession, mode: JudgeMode
) -> tuple[SalesSegmentRunModel, Candidate, Candidate]:
    """Прогон с письмами на русском: украинская мастерская — отказ языка, русская — модели «да»."""
    owner = await hypothesis(session, "Мастерские ремонта велосипедов: сами чинят велосипеды.")
    run = await make_run(session, owner, mode=mode)
    run.language = "ru"
    await session.commit()
    uk = await _candidate(session, run, UK_HOST, UK)
    ru = await _candidate(session, run, RU_HOST, RU)
    await judge_run(
        session, run_id=run.id, segment=owner.description or "", mode=mode,
        client=_judge(Model({RU_HOST: RU_ANSWER})), home=None, letters=run.language,
    )  # fmt: skip
    await session.refresh(uk)
    await session.refresh(ru)
    return run, uk, ru


async def test_a_language_refusal_comes_back_from_an_undo_as_language_and_not_as_a_rule(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Вердикт языка в кэш не ложится и модели у него нет — как у правила. Отмена решения
    отсекла бы такой сайт в любом режиме, назвав решившим правило; теперь сверка повторяется по
    сохранённому тексту и языку писем прогона — сайт там же и с теми же словами."""
    run, uk, ru = await _language_run(session, JudgeMode.SHADOW)
    assert (uk.lane, uk.decided_by, uk.reason) == (
        Lane.WOULD_REJECT,
        Decider.LANGUAGE,
        LANGUAGE_WORDS,
    )
    assert ru.lane == Lane.ACCEPT
    url = f"{CANDIDATES}/{uk.id}/decision"
    accepted = await client.post(url, json={"accept": True}, headers=headers)
    assert accepted.json()["judge_reason"] == LANGUAGE_WORDS

    back = await client.delete(url, headers=headers)

    assert back.status_code == 200, back.text
    row = back.json()
    assert (row["lane"], row["decided_by"], row["reason"], row["verdict"]) == (
        "would_reject",
        "language",
        LANGUAGE_WORDS,
        "reject",
    )
    listed = await client.get(
        f"{RUNS}/{run.id}/sites?lane=would_reject&decider=language", headers=headers
    )
    assert [found["host"] for found in listed.json()["rows"]] == [UK_HOST]


async def test_a_language_cut_in_enforce_is_not_undone_and_says_who_decided(
    session: AsyncSession,
) -> None:
    _, uk, _ = await _language_run(session, JudgeMode.ENFORCE)
    assert (uk.lane, uk.decided_by) == (Lane.REJECT, Decider.LANGUAGE)

    with pytest.raises(su.UndoRefusedError) as refused:
        await su.undo(session, uk.id)

    assert str(refused.value) == su.NOT_HUMAN[Decider.LANGUAGE]


# --- пересуд: старый отказ «на проверке» — в свою полосу ---------------------------------------------


async def test_a_rejudge_moves_an_old_refusal_from_review_into_its_own_lane_and_names_it(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner, mode=JudgeMode.SHADOW)
    old = await old_site(
        session, run, OLD_NEWS, lane=Lane.REVIEW, verdict=Lane.REJECT,
        reason="Отказ: пишет о чужих",
    )  # fmt: skip
    with_lead = await old_site(session, run, OLD_SHOP, lane=Lane.ACCEPT)
    await lead_of(session, with_lead)
    await asked(session, run)
    no_shop = {"fit": "no", "quote": "интернет-магазин Велодом", "why": "продаёт только самокаты"}

    await sr.rejudge(session, run.id, deps(Model({OLD_NEWS: NO_NEWS, OLD_SHOP: no_shop})))

    await session.refresh(old)
    await session.refresh(with_lead)
    assert (old.lane, old.reason) == (Lane.WOULD_REJECT, "пишет о чужих")
    assert with_lead.lane == Lane.ACCEPT  # у сайта с лидом полоса не меняется
    found = changes(await report(session, run))
    assert found == {
        old.id: Change("review", "would_reject"),
        with_lead.id: Change(
            "accept", "would_reject", True, "продаёт только самокаты", "интернет-магазин Велодом"
        ),
    }
    moved = (summary(await report(session, run)) or {})["moved"]
    assert sorted((move["was"], move["now"], move["would"]) for move in moved) == [
        ("accept", "would_reject", True),
        ("review", "would_reject", False),
    ]


# --- экран знает каждую полосу сервера ---------------------------------------------------------------


def _union(path: Path, name: str) -> set[str]:
    """Значения типа экрана `export type <name> = 'a' | 'b';` — файл читается как текст."""
    found = re.search(rf"export type {name} =([^;]+);", path.read_text(encoding="utf-8"))
    assert found is not None, f"в {path.name} нет типа {name}"
    return set(re.findall(r"'([a-z_]+)'", found.group(1)))


def test_the_screen_knows_every_lane_tab_and_decider_of_the_server() -> None:
    """Полоса или «кто решил», которых экран не знает, — пустой значок и пустое слово без ошибки:
    так было с «язык» у «кто решил», пока его не назвали на экране."""
    assert _union(FRONT / "segmentApi.ts", "SegmentLane") == {lane.value for lane in Lane}
    assert _union(FRONT / "SegmentSitesApi.ts", "SiteTab") == {tab.value for tab in SiteLane}
    assert _union(FRONT / "SegmentSitesApi.ts", "SiteDecider") == {
        decider.value for decider in Decider
    }
    assert "lane" in SiteView.model_fields
