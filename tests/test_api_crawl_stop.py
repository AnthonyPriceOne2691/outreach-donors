"""Почему обход кончился — в строке обхода; потолки обхода — числами настроек.

Тест-группа 23.10.2026 видела «обойдён: N страниц» и «частично: N страниц» и не
понимала, почему не 1 000: у одного донора кончилась карта сайта, у другого сайт просит
долгую паузу, и за час на донора не набирается и половины потолка. Откуда брались адреса, дочитана ли
карта и какую паузу просит сайт, лежало в отчёте обхода, но до экрана не доходило; потолки
экран знал только страничный. Строка обхода несёт их везде, где её рисуют: панель обхода,
последние обходы, «Приёмка по донорам».
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.api.crawls import routes
from backend.config import crawl as crawl_cfg
from backend.features.core.domain import CrawlOutcome, CrawlStatus, StopReason
from backend.features.core.models.access import UserModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.donor import DonorModel
from backend.features.crawl.board import CrawlLine
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor

DONOR = "paused.example.test"

#: Отчёт обхода, упёршегося в час: карта прочитана не вся, сайт просит паузу 6 с.
TIMED_OUT = {
    "source": "sitemap",
    "sitemap_complete": False,
    "crawl_delay": 6,
    "elapsed_sec": 3600.9,
}

#: Поля строки, которые говорят, почему обход кончился, и потолки обхода.
WHY = ("source", "sitemap_complete", "crawl_delay", "elapsed_sec", "time_budget_sec")
CEILINGS = ("max_attempts", "max_seconds", "delay_sec", "max_delay_sec")


@pytest.fixture(autouse=True)
def no_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Число обходчиков спрашивается у очереди — здесь не о нём."""

    async def none() -> None:
        return None

    monkeypatch.setattr(routes, "_workers", none)


@pytest.fixture
async def viewer(
    make_user: Callable[..., Awaitable[UserModel]], sign_in: Callable[..., Awaitable[str]]
) -> dict[str, str]:
    await make_user("смотрит@site.com", permissions={"run": False})
    return bearer(await sign_in("смотрит@site.com"))


def finished(stats: dict[str, Any], *, host: str = DONOR) -> CrawlRunModel:
    return CrawlRunModel(
        host=host,
        status=CrawlStatus.DONE,
        outcome=CrawlOutcome.PARTIAL,
        stop_reason=StopReason.TIMEOUT,
        pages_opened=412,
        articles=400,
        finished_at=datetime.now(UTC),
        stats=stats,
    )


def pick(row: dict[str, Any], names: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(row[name] for name in names)


async def test_a_finished_crawl_says_why_and_the_ceilings_are_the_settings(
    client: AsyncClient,
    session: AsyncSession,
    viewer: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Потолки — настройками сервера, а не копией в экране: у панели («i» называет их и без
    строк обходов) и у каждой строки обхода (значок называет тот, в который упёрся обход)."""
    for name, value in (
        ("MAX_ATTEMPTS_PER_DONOR", 1500),
        ("MAX_SECONDS_PER_DONOR", 1800.0),
        ("DELAY_SEC", 2.0),
        ("MAX_DELAY_SEC", 5.0),
    ):
        monkeypatch.setattr(crawl_cfg, name, value)
    domain = await make_donor(session, DONOR)
    donor = (
        await session.execute(select(DonorModel).where(DonorModel.domain_id == domain.id))
    ).scalar_one()
    donor.last_price, donor.last_price_at = Decimal("150"), datetime.now(UTC) - timedelta(days=3)
    session.add(finished(TIMED_OUT))
    await session.commit()

    [row] = (await client.get("/api/crawls", headers=viewer)).json()["rows"]
    targets = (await client.get("/api/crawls/targets", headers=viewer)).json()

    assert (row["stop_reason"], row["pages"]) == ("timeout", 412)
    assert pick(row, WHY) == ("sitemap", False, 6.0, 3600.9, None)
    assert pick(targets["donors"][0]["crawl"], WHY) == pick(row, WHY)
    for said in (row, targets, targets["donors"][0]["crawl"]):
        assert pick(said, CEILINGS) == (1500, 1800.0, 2.0, 5.0)


async def test_running_and_old_crawls_say_nothing_about_why(
    client: AsyncClient, session: AsyncSession, viewer: dict[str, str]
) -> None:
    """Идущий обход ещё не кончился, а записанный до отчёта о нём не знает: пусто, а не
    «карты нет» и не «пауза 0 с»."""
    running = CrawlRunModel(
        host="running.example.test",
        status=CrawlStatus.RUNNING,
        pages_opened=12,
        stats={"reason": "воркер умер; обход продолжен с чекпоинта"},
    )
    session.add_all([running, finished({"links_found": 3}, host="old.example.test")])
    await session.commit()

    rows = (await client.get("/api/crawls", headers=viewer)).json()["rows"]

    assert {row["host"]: pick(row, WHY) for row in rows} == {
        "running.example.test": (None,) * len(WHY),
        "old.example.test": (None,) * len(WHY),
    }


async def test_acceptance_draws_the_same_crawl_row(
    client: AsyncClient, session: AsyncSession, viewer: dict[str, str]
) -> None:
    """«Приёмка по донорам» собирает строку обхода своим маршрутом — и получает то же:
    значок обхода у неё тот же, что на панели."""
    session.add(finished({**TIMED_OUT, "time_budget_sec": 10800}))
    await session.commit()

    body = (await client.get("/api/advertisers/acceptance", headers=viewer)).json()

    crawl = {row["host"]: row["crawl"] for row in body["donors"]}[DONOR]
    assert pick(crawl, WHY) == ("sitemap", False, 6.0, 3600.9, 10800.0)
    assert pick(crawl, CEILINGS[:2]) == (
        crawl_cfg.MAX_ATTEMPTS_PER_DONOR,
        crawl_cfg.MAX_SECONDS_PER_DONOR,
    )


@pytest.mark.parametrize(
    ("stats", "said"),
    [
        ({"source": "links", "sitemap_complete": True}, ("links", True, None, None, None)),
        ({"source": "sitemap+links", "crawl_delay": 0}, ("sitemap+links", None, 0.0, None, None)),
        # Обход не дошёл до списка адресов — источника нет.
        ({"source": "none", "elapsed_sec": 2}, (None, None, None, 2.0, None)),
        # Не то, что пишет отчёт, — пусто, а не догадка.
        ({"source": ["sitemap"], "sitemap_complete": "false"}, (None,) * 5),
        ({"crawl_delay": True, "elapsed_sec": "12", "time_budget_sec": -1}, (None,) * 5),
    ],
)
def test_a_line_takes_only_what_the_report_writes(
    stats: dict[str, Any], said: tuple[Any, ...]
) -> None:
    line = CrawlLine.of(finished(stats))

    assert tuple(getattr(line, name) for name in WHY) == said
