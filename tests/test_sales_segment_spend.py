"""Расход прогона сбора рядом со сметой (`sales/segment_spend.py`): выдача в долларах, судья в
токенах и долларах, Hunter — запросами поиска и проверками; этапа, которого не было, — словами,
а не нулём; продолжение прогона тратит снова — расход за все проходы.

Отчёт и смета прогона — словари, как их пишет прогон; два прохода — настоящим прогоном на базе
с подделками выдачи, модели и сайтов (`test_sales_segment_run`). Выдача, которую ещё покупают, —
«покупка не закончена», а не «цена не записана»: снимок панели посреди настоящей покупки
отложенного режима (номера задач уже в отчёте, итога нет). Числа выдуманы.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
from backend.config import sales as sales_cfg
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.segment_spend import (
    AFTER_SERP,
    BUYING,
    SpendRow,
    number,
    spend_rows,
    usd_per_million,
)
from backend.features.serp.protocol import SerpResult, SerpWatch
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_segment_api import headers
from tests.test_sales_segment_progress import Queued
from tests.test_sales_segment_run import (
    ANSWERS,
    NEWS,
    SHOP,
    Model,
    Sites,
    _network,
    go,
    hypothesis,
    make_run,
    serp,
    web,
)

#: Фикстуры тестов прогона: вход учётки, сайты и сеть-подделка (DNS — таблицей, платной ступени нет).
__all__ = ["_network", "headers", "web"]

#: Смета на запуске: выдача, токены и их деньги по цене миллиона, поиски и проверки Hunter.
ESTIMATE: dict[str, Any] = {
    "serp_usd": 0.0105,
    "tokens": 61_600,
    "tokens_usd": 0.037,
    "usd_per_1m_tokens": 0.6,
    "hunter_searches": 56,
    "hunter_checks": 56,
    "lines": ["смета"],
}
#: Отчёт прогона, прошедшего все этапы.
REPORT: dict[str, Any] = {
    "serp": {"keywords": 5, "hosts": 41, "cost_usd": 0.0132},
    "judge": {"judged": 38, "tokens": 40_211},
    "contacts": {"counters": {"provider_entered": 17, "provider_refused": 3}},
    "leads": {"created": 11, "cleaning": {"paid_units": 11, "verifier": "hunter"}},
}


def run_of(
    *,
    estimate: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
    mode: str = "shadow",
) -> SalesSegmentRunModel:
    return SalesSegmentRunModel(
        hypothesis_id=1,
        keywords=["купить горный велосипед"],
        country="us",
        language="en",
        depth_pages=1,
        mode=mode,
        estimate=ESTIMATE if estimate is None else estimate,
        report=REPORT if report is None else report,
    )


def rows_of(run: SalesSegmentRunModel) -> dict[str, SpendRow]:
    return {row.item: row for row in spend_rows(run)}


@pytest.fixture(autouse=True)
def _price(monkeypatch: pytest.MonkeyPatch) -> None:
    """Цена токена в настройках — другая, чем в смете: панель обязана брать сметную."""
    monkeypatch.setattr(sales_cfg, "SEGMENT_INPUT_USD_PER_1M_TOKENS", 1.25)
    monkeypatch.setattr(sales_cfg, "SEGMENT_OUTPUT_USD_PER_1M_TOKENS", 10.0)


def test_every_row_comes_from_the_report_with_the_estimate_beside_it() -> None:
    found = spend_rows(run_of())

    assert [(row.item, row.title, row.fact, row.promised) for row in found] == [
        ("serp", "Выдача", "$0,0132", "≈ $0,0105"),
        (
            "judge",
            "Судья сегмента",
            "токенов 40\u00a0211, ≈ $0,0241",
            "токенов ≈ 61\u00a0600, ≈ $0,0370",
        ),
        (
            "search",
            "Hunter: поиск адреса",
            "поисков 14; отказов провайдера 3 — их он не засчитывает",
            "до 56",
        ),
        ("check", "Hunter: проверка адреса", "проверок 11", "до 56"),
        ("money", "Деньгами: выдача и модель", "$0,0373", "≈ $0,0475"),
    ]
    by_item = {row.item: row for row in found}
    assert (by_item["serp"].fact_usd, by_item["serp"].promised_usd) == (0.0132, 0.0105)
    assert (by_item["judge"].fact_units, by_item["judge"].fact_usd) == (40_211, 0.0241)
    assert (by_item["search"].fact_units, by_item["search"].promised_units) == (14, 56)
    assert (by_item["check"].fact_units, by_item["check"].promised_units) == (11, 56)
    assert (by_item["money"].fact_usd, by_item["money"].promised_usd) == (0.0373, 0.0475)


def test_digit_groups_are_joined_by_a_no_break_space() -> None:
    """«11 310» не рвётся посреди числа ни в узкой колонке окна расхода, ни в карточке на телефоне:
    разряды — неразрывным пробелом, как пишет числа экран (`Intl` для ru-RU). Обычного пробела
    внутри числа нет ни в факте, ни в смете."""
    assert (number(999), number(11_310), number(1_234_567)) == (
        "999",
        "11\u00a0310",
        "1\u00a0234\u00a0567",
    )
    for row in spend_rows(run_of()):
        assert re.search(r"\d \d", row.fact + row.promised) is None, row


def test_the_judge_money_takes_the_price_of_the_launch_and_of_the_settings_for_old_runs() -> None:
    """Цена на запуске — в смете прогона: правка настройки потом прошлые отчёты не переписывает.
    У прогона до цены в смете — цена настроек, и смета денег модели не называла."""
    launched = rows_of(run_of())["judge"]
    old = rows_of(run_of(estimate={"tokens": 61_600, "lines": ["смета"]}))["judge"]

    assert launched.fact_usd == round(40_211 * 0.6 / 1_000_000, 4)
    assert usd_per_million() == pytest.approx(0.8 * 1.25 + 0.2 * 10.0)
    assert old.fact_usd == round(40_211 * usd_per_million() / 1_000_000, 4)
    assert (old.promised, old.promised_usd) == ("токенов ≈ 61\u00a0600", None)


@pytest.mark.parametrize(
    ("report", "facts"),
    [
        (
            {},
            [
                "выдачу ещё не покупали",
                "судья ещё не судил",
                "адреса ещё не искали",
                "адреса ещё не проверяли",
                "трат ещё не было",
            ],
        ),
        (
            {key: REPORT[key] for key in ("serp", "judge")},
            [
                "$0,0132",
                "токенов 40\u00a0211, ≈ $0,0241",
                "адреса ещё не искали",
                "адреса ещё не проверяли",
                "$0,0373",
            ],
        ),
    ],
    ids=["queued", "stopped-after-the-judge"],
)
def test_a_stage_the_run_has_not_reached_is_named_not_zeroed(
    report: dict[str, Any], facts: list[str]
) -> None:
    """Ноль сказал бы «обошлось бесплатно» о том, чего ещё не было."""
    found = spend_rows(run_of(report=report))

    assert [row.fact for row in found] == facts
    assert [row.fact_units for row in found if row.item in ("search", "check")] == [None, None]


def test_nothing_paid_is_said_in_words_of_why() -> None:
    """Нечего искать платно и проверка выдуманная — «не было», а не пустая клетка; смета — тоже
    словами: платный поиск выключен, проверка выдуманная."""
    found = rows_of(
        run_of(
            estimate={**ESTIMATE, "hunter_searches": 0, "hunter_checks": 0},
            report={
                **REPORT,
                "contacts": {"pending": 0, "walked": 0, "saved": 0},
                "leads": {"created": 3, "cleaning": {"paid_units": 0, "verifier": "fixture"}},
            },
        )
    )

    assert (found["search"].fact, found["search"].promised) == (
        "платных поисков не было",
        "платный поиск выключен",
    )
    assert (found["check"].fact, found["check"].promised) == (
        "проверка выдуманная — не платили",
        "проверка выдуманная",
    )
    no_leads = rows_of(run_of(report={**REPORT, "leads": {"created": 0}}))["check"]
    assert (no_leads.fact, no_leads.fact_units) == ("лидов не заводили — проверок не было", 0)


def test_an_unrecorded_price_is_not_a_zero() -> None:
    """Выдача куплена, а цены в отчёте нет — «не записана», и в деньгах вместе её нет."""
    found = rows_of(run_of(report={**REPORT, "serp": {"keywords": 5, "hosts": 41}}))

    assert (found["serp"].fact, found["serp"].fact_usd) == ("цена не записана", None)
    assert found["money"].fact_usd == found["judge"].fact_usd


#: След покупки отложенного режима: номер задачи ключа — в отчёте до опроса.
TASKS = {"купить горный велосипед": "task-0"}


@pytest.mark.parametrize(
    "report",
    [
        {"serp": {"tasks": TASKS}, "progress": {"stage": "serp", "done": 0, "total": 1}},
        {"progress": {"stage": "serp", "done": 0, "total": 3, "mode": "live"}},
        {"serp": {"tasks": TASKS}},
    ],
    ids=["standard-polled", "live-buying", "trail-without-progress"],
)
def test_a_serp_being_bought_is_not_an_unrecorded_price(report: dict[str, Any]) -> None:
    """Покупка выдачи начата, итога нет — «покупка не закончена»: не «цена не записана» все
    минуты очереди провайдера и не «ещё не покупали» посреди быстрой покупки. В деньгах вместе —
    «после покупки», а не «трат ещё не было»: задачи отложенного режима уже оплачены."""
    found = rows_of(run_of(report=report))

    assert (found["serp"].fact, found["serp"].fact_usd) == (BUYING, None)
    assert (found["money"].fact, found["money"].fact_usd) == (AFTER_SERP, None)


def test_a_serp_paid_before_a_failure_is_money_while_it_is_bought_again() -> None:
    """Сбой после оплаты, повтор ещё покупает: оплаченное — деньгами, как в журнале."""
    paid = {"tasks": TASKS, "paid_usd": 0.0045, "cost_usd": 0.0045}
    found = rows_of(run_of(report={"serp": paid, "progress": {"stage": "serp"}}))

    assert (found["serp"].fact, found["serp"].fact_usd) == ("$0,0045", 0.0045)
    assert (found["money"].fact, found["money"].fact_usd) == ("$0,0045", 0.0045)


@dataclass
class Polled(Queued):
    """Выдача отложенного режима: снимок панели расхода — сразу после того, как номера задач
    легли в отчёт прогона, до опроса и итога покупки."""

    during: dict[str, SpendRow] = field(default_factory=dict)

    async def search(
        self,
        keywords: Sequence[str],
        country: str,
        *,
        depth_pages: int = 1,
        watch: SerpWatch | None = None,
    ) -> dict[str, list[SerpResult]]:
        assert watch is not None
        assert watch.on_posted is not None
        assert self.run is not None
        trail, run = watch.on_posted, self.run

        async def posted(created: Mapping[str, str]) -> None:
            await trail(dict(created))
            self.during = rows_of(run)

        polled = SerpWatch(tasks=watch.tasks, on_posted=posted, on_progress=watch.on_progress)
        return await super().search(keywords, country, depth_pages=depth_pages, watch=polled)


async def test_the_panel_says_the_serp_is_being_bought_while_its_tasks_are_polled(
    session: AsyncSession, web: Sites
) -> None:
    """Стык хода прогона и панели: номера задач в отчёте, итога нет — «покупка не закончена»;
    выдача куплена — цена, как в журнале."""
    run = await make_run(session, await hypothesis(session))
    found = Polled({}, price=0.0045, run=run)

    await go(session, run, found, Model(ANSWERS))

    assert (found.during["serp"].fact, found.during["money"].fact) == (BUYING, AFTER_SERP)
    after = rows_of(run)
    assert (after["serp"].fact, after["money"].fact_usd) == ("$0,0045", 0.0045)


def test_a_switched_off_judge_costs_no_model_and_money_is_the_serp() -> None:
    found = rows_of(run_of(mode="off", report={**REPORT, "judge": {"tokens": 0}}))

    assert (found["judge"].fact, found["judge"].promised) == ("модель не звалась", "судья выключен")
    assert (found["money"].fact, found["money"].promised) == ("$0,0132", "≈ $0,0105")


async def test_a_continued_run_sums_the_judge_spend_of_both_passes(
    session: AsyncSession, web: Sites
) -> None:
    """Первый проход: модель не ответила по магазину (новости — 613 токенов); второй — досудила
    его (701). Расход судьи — за оба прохода, а не последний."""
    run = await make_run(session, await hypothesis(session))
    run.estimate = {**ESTIMATE, "usd_per_1m_tokens": 0.6}
    found = serp(SHOP, NEWS)

    await go(session, run, found, Model({**ANSWERS, SHOP: 503}, tokens=613))
    await go(session, run, found, Model(ANSWERS, tokens=701))

    judge = rows_of(run)["judge"]
    assert (judge.fact_units, judge.fact_usd) == (613 + 701, round(1314 * 0.6 / 1_000_000, 4))
    assert rows_of(run)["serp"].fact == "$0,0037"  # выдача куплена один раз


async def test_the_run_report_carries_its_keywords_and_spend(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner, keywords=["купить горный велосипед", "велосипед москва"])
    run.estimate = {
        **ESTIMATE,
        "rejected": {"site:shop.example.test велосипед": "поисковый оператор"},
        "notes": {"велосипед москва": "похож на «купить горный велосипед»"},
    }
    run.report = {**REPORT, "serp": {**REPORT["serp"], "lost_keywords": ["велосипед москва"]}}
    await session.commit()

    response = await client.get(f"/api/sales/segment/runs/{run.id}", headers=headers)

    assert response.status_code == 200, response.text
    spend = response.json()["spend"]
    assert spend["keywords"] == {
        "taken": ["купить горный велосипед", "велосипед москва"],
        "notes": {"велосипед москва": "похож на «купить горный велосипед»"},
        "rejected": {"site:shop.example.test велосипед": "поисковый оператор"},
        "lost": ["велосипед москва"],
        "empty": [],
        "excluded": [],  # снятых на запуске нет: смета прогона старше снятия
    }
    assert [row["item"] for row in spend["rows"]] == ["serp", "judge", "search", "check", "money"]
    assert spend["rows"][0] == {
        "item": "serp",
        "title": "Выдача",
        "fact": "$0,0132",
        "promised": "≈ $0,0105",
        "fact_units": None,
        "fact_usd": 0.0132,
        "promised_units": None,
        "promised_usd": 0.0105,
    }
