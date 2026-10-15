"""Свой потолок Этапа 2 на платный обход и журнал его трат.

Расход Этапа 2 на обход и прокси держится своим потолком, отдельно от Этапа 1: иначе
один донор за защитой съест месяц. Проверяется: цена вызова — по настройке;
потолок спрашивается до вызова тем же числом, что показывает экран; трата пишется сразу
и своей транзакцией — её видят соседние обходчики; задача обхода ведёт журнал сама.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from backend.config import crawl as crawl_cfg
from backend.config import storage
from backend.features.core import usage
from backend.features.core.domain import Stage, UsageProvider
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.crawl import proxies as proxies_module
from backend.features.crawl.spend import (
    MAX_UNITS,
    PROXY_OPERATION,
    RENDER_OPERATION,
    JournalSpending,
    cost_of,
)
from backend.features.ops.silence import alarms
from backend.features.outreach.repository import EVERY_STAGE
from backend.workers import crawl_jobs
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import TEST_DSN
from tests.test_crawl_jobs import _queued, _site
from tests.test_send_race import committed_sessions

GATE = "http://user:secret@gate.example.test:7777"  # pragma: allowlist secret
HTML = {"content-type": "text/html; charset=utf-8"}


class TestCost:
    def test_proxy_traffic_is_paid_by_the_gigabyte(self) -> None:
        cost = cost_of(PROXY_OPERATION, 2_000_000_000, proxy_usd_per_gb=5.0, render_usd_per_1k=None)

        assert (cost.units, cost.amount_usd) == (2_000_000_000, Decimal("10.0"))

    def test_render_call_is_paid_by_the_thousand(self) -> None:
        cost = cost_of(RENDER_OPERATION, 1, proxy_usd_per_gb=None, render_usd_per_1k=16.0)

        assert (cost.units, cost.amount_usd) == (1, Decimal("0.016"))

    def test_a_fraction_of_a_cent_is_rounded_up_not_lost(self) -> None:
        """5 КБ по $5 за гигабайт — $0,000025: обычное округление дало бы ноль, и двадцать
        тысяч таких страниц прошли бы мимо своего потолка."""
        cost = cost_of(PROXY_OPERATION, 5_000, proxy_usd_per_gb=5.0, render_usd_per_1k=None)

        assert cost.amount_usd == Decimal("0.0001")

    def test_unknown_price_writes_units_without_money(self) -> None:
        """Пустое поле денег — «цена не задана», и единицы при этом видны."""
        cost = cost_of(PROXY_OPERATION, 123_456, proxy_usd_per_gb=None, render_usd_per_1k=None)

        assert (cost.units, cost.amount_usd) == (123_456, None)

    def test_units_fit_the_column(self) -> None:
        cost = cost_of(PROXY_OPERATION, 10**12, proxy_usd_per_gb=None, render_usd_per_1k=None)

        assert cost.units == MAX_UNITS


async def _spent(factory: async_sessionmaker[AsyncSession], amount: str, *, ago: int = 0) -> None:
    async with factory() as session:
        row = usage.record(session, operation=PROXY_OPERATION, units=1, amount_usd=Decimal(amount))
        await session.flush()
        if ago:
            row.created_at = datetime.now(UTC).replace(day=1) - timedelta(days=ago)
        await session.commit()


class TestJournal:
    async def test_without_its_own_cap_paid_levels_are_not_called(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Потолок держит трату; нет его — держать нечем. Отказ словами, имя настройки —
        в журнале, и один раз на обход, а не на каждую закрытую страницу."""
        monkeypatch.setattr(crawl_cfg, "MONTH_CAP_USD", None)
        async with committed_sessions() as factory:
            ledger = JournalSpending(factory, proxy_usd_per_gb=5.0, render_usd_per_1k=1.0)

            with caplog.at_level(logging.WARNING):
                first = await ledger.refusal(PROXY_OPERATION)
                again = await ledger.refusal(PROXY_OPERATION)
                render = await ledger.refusal(RENDER_OPERATION)

        assert first == again
        assert first is not None
        assert first.startswith("у Этапа 2 не задан свой потолок")
        assert "CRAWL_MONTH_CAP_USD" not in first
        assert render is not None
        assert caplog.text.count("CRAWL_MONTH_CAP_USD") == 2, "по разу на уровень, не на страницу"

    async def test_cap_without_a_price_does_not_spend_blind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(crawl_cfg, "RENDER_USD_PER_1K", None)
        async with committed_sessions() as factory:
            ledger = JournalSpending(factory, cap_usd=50.0, proxy_usd_per_gb=5.0)

            refusal = await ledger.refusal(RENDER_OPERATION)

        assert refusal is not None
        assert "цена вызова сервиса рендеринга не задана — тратить вслепую нельзя" in refusal
        assert "CRAWL_RENDER_USD_PER_1K" not in refusal

    async def test_cap_counts_this_month_and_stops_at_the_ceiling(self) -> None:
        async with committed_sessions() as factory:
            ledger = JournalSpending(factory, cap_usd=10.0, proxy_usd_per_gb=5.0)
            await _spent(factory, "50", ago=3)  # прошлый месяц не в счёт
            await _spent(factory, "9.99")
            below = await ledger.refusal(PROXY_OPERATION)
            await _spent(factory, "0.01")
            reached = await ledger.refusal(PROXY_OPERATION)

        assert below is None
        assert reached is not None
        assert "потолок Этапа 2 на обход выбран: 10.00 из 10.00 $" in reached

    async def test_a_reached_ceiling_is_remembered_for_both_levels(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Месяц назад не вернётся: после «выбран» обход не считает сумму и не предупреждает
        на каждой странице, а второй уровень узнаёт об этом без своей суммы."""
        async with committed_sessions() as factory:
            ledger = JournalSpending(
                factory, cap_usd=1.0, proxy_usd_per_gb=5.0, render_usd_per_1k=1.0
            )
            await _spent(factory, "1.00")
            with caplog.at_level(logging.WARNING):
                proxy = await ledger.refusal(PROXY_OPERATION)
                async with factory() as session:
                    await session.execute(delete(UsageRecordModel))
                    await session.commit()
                render = await ledger.refusal(RENDER_OPERATION)
                again = await ledger.refusal(PROXY_OPERATION)

        assert proxy == render == again
        assert proxy is not None
        assert proxy.startswith("потолок Этапа 2 на обход выбран")
        assert caplog.text.count("потолок Этапа 2 на обход выбран") == 1

    async def test_spend_is_committed_at_once_as_stage_two(self) -> None:
        """Сразу и своей транзакцией: соседний обходчик видит трату до конца пачки."""
        async with committed_sessions() as factory:
            ledger = JournalSpending(factory, cap_usd=10.0, proxy_usd_per_gb=4.0)

            await ledger.spent(PROXY_OPERATION, 250_000_000)

            async with factory() as session:
                row = (await session.execute(select(UsageRecordModel))).scalar_one()
        assert (row.provider, row.stage, row.units) == (
            UsageProvider.PROXY,
            Stage.ADVERTISERS,
            250_000_000,
        )
        assert row.amount_usd == Decimal("1.0000")


@pytest.fixture
def crawl_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr(crawl_cfg, "DELAY_SEC", 0.0)
    monkeypatch.setattr(crawl_cfg, "BROWSER_ENABLED", False)
    monkeypatch.setattr(crawl_cfg, "PROXY_URLS", (GATE,))
    monkeypatch.setattr(crawl_cfg, "PROXY_USD_PER_GB", 5.0)
    monkeypatch.setattr(crawl_cfg, "RENDER_API_KEY", "")
    monkeypatch.setattr(crawl_jobs, "quick_queue", _NoQueue)
    crawl_jobs._STOP.clear()


class _NoQueue:
    def enqueue(self, *_: Any, **__: Any) -> Any:
        return type("Job", (), {"id": "judge"})()


def _closed_page_and_gate(monkeypatch: pytest.MonkeyPatch, path: str = "/p1") -> list[str]:
    """Сайт закрывает одну страницу напрямую (403), а прокси-шлюз в памяти её отдаёт.
    Возвращает журнал шлюза: какие страницы прошли через прокси."""
    site = _site(monkeypatch)
    page = site.pages[path]

    def direct(request: httpx.Request) -> httpx.Response:
        if request.url.path == path:
            return httpx.Response(403, text="нет", headers=HTML)
        return site.handler(request)

    asked: list[str] = []

    def gate(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(200, text=page, headers=HTML)

    monkeypatch.setattr(
        crawl_jobs,
        "guarded_client",
        lambda **_: httpx.AsyncClient(transport=httpx.MockTransport(direct)),
    )
    monkeypatch.setattr(proxies_module, "proxy_transport", lambda _url: httpx.MockTransport(gate))
    return asked


@pytest.mark.usefixtures("crawl_wiring")
async def test_crawl_job_pays_the_proxy_into_the_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Задача обхода ведёт журнал сама: страница, закрытая напрямую, открыта прокси,
    и его трафик — строкой Этапа 2 с деньгами по цене гигабайта."""
    asked = _closed_page_and_gate(monkeypatch)
    monkeypatch.setattr(crawl_cfg, "MONTH_CAP_USD", 100.0)
    async with committed_sessions() as factory:
        run_id = await _queued(factory)

        done = await crawl_jobs._crawl(run_id)

        async with factory() as session:
            rows = (await session.execute(select(UsageRecordModel))).scalars().all()
    assert done["outcome"] == "ok"
    assert asked == ["/p1"]
    assert [(row.operation, row.stage) for row in rows] == [(PROXY_OPERATION, Stage.ADVERTISERS)]
    assert rows[0].units is not None
    assert rows[0].units > 1_000
    assert rows[0].amount_usd is not None
    assert rows[0].amount_usd >= 0


@pytest.mark.usefixtures("crawl_wiring")
async def test_spent_ceiling_keeps_the_proxy_silent_and_marks_the_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _closed_page_and_gate(monkeypatch)
    monkeypatch.setattr(crawl_cfg, "MONTH_CAP_USD", 1.0)
    async with committed_sessions() as factory:
        await _spent(factory, "1.00")
        run_id = await _queued(factory)

        await crawl_jobs._crawl(run_id)

        async with factory() as session:
            run = await session.get(CrawlRunModel, run_id)
            rows = (await session.execute(select(UsageRecordModel))).scalars().all()
    assert asked == []
    assert len(rows) == 1, "новых трат нет — только та, что выбрала потолок"
    assert run is not None
    assert run.degradation is not None
    assert "потолок Этапа 2 на обход выбран" in run.degradation["proxy"]


@pytest.mark.usefixtures("crawl_wiring")
async def test_no_cap_keeps_the_proxy_silent_and_says_so_in_the_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Прокси и цена заданы, а своего потолка нет: прокси не зовётся, обход идёт бесплатными,
    а в записи обхода — почему."""
    asked = _closed_page_and_gate(monkeypatch)
    monkeypatch.setattr(crawl_cfg, "MONTH_CAP_USD", None)
    async with committed_sessions() as factory:
        run_id = await _queued(factory)

        await crawl_jobs._crawl(run_id)

        async with factory() as session:
            run = await session.get(CrawlRunModel, run_id)
            rows = (await session.execute(select(UsageRecordModel))).scalars().all()
    assert asked == []
    assert rows == []
    assert run is not None
    assert run.degradation is not None
    assert run.degradation["proxy"].startswith("у Этапа 2 не задан свой потолок")


class TestWatchdog:
    async def test_a_reached_stage_two_ceiling_is_an_alarm(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Как у Ahrefs: закрытые сайты стоят необойдёнными не потому, что сломалось."""
        monkeypatch.setattr(crawl_cfg, "MONTH_CAP_USD", 5.0)
        usage.record(session, operation=PROXY_OPERATION, units=10, amount_usd=Decimal("5.00"))
        await session.flush()

        found = await alarms(session, stages=EVERY_STAGE)

        [alarm] = [alarm for alarm in found if alarm.code == "crawl-cap-reached"]
        assert alarm.title == "Потолок Этапа 2 на обход выбран"
        assert "5.00 $ при своём потолке 5.00 $" in alarm.detail

    @pytest.mark.parametrize(("cap", "spent"), [(None, "99.00"), (5.0, "4.99")])
    async def test_no_alarm_below_the_ceiling_or_without_one(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        cap: float | None,
        spent: str,
    ) -> None:
        monkeypatch.setattr(crawl_cfg, "MONTH_CAP_USD", cap)
        usage.record(session, operation=PROXY_OPERATION, units=10, amount_usd=Decimal(spent))
        await session.flush()

        found = await alarms(session, stages=EVERY_STAGE)

        assert "crawl-cap-reached" not in {alarm.code for alarm in found}
