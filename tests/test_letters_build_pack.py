"""Сборка писем пачками на настоящей базе (`letters/building.py`).

Пачка — `OUTREACH_BUILD_CONCURRENCY` вызовов модели разом. Проверяется то, ради
чего она устроена именно так: вызовы пачки идут одновременно, письма ложатся
в очередь в порядке отбора, а не в порядке ответов модели, упавший вызов не
теряет соседей, потолок расхода останавливает следующую пачку, а 1 возвращает
прежнюю сборку по одному.
"""

from __future__ import annotations

import asyncio

import pytest
from backend.config import llm as llm_cfg
from backend.config import outreach as outreach_cfg
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.building import BuildReport, BuildRequest, QueueBuilder
from backend.features.letters.rewrite import RewriteResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

#: По убыванию DR — так их отдаёт отбор.
HOSTS = ("a.example.test", "b.example.test", "c.example.test", "d.example.test", "e.example.test")
TOKENS = 120


class SlowRewriter:
    """Модель, которая отвечает тем позже, чем раньше её позвали: внутри пачки
    ответы приходят в обратном порядке. Считает вызовы, идущие одновременно."""

    def __init__(self, *, fail: str | None = None) -> None:
        self.fail = fail
        self.seen: list[str] = []
        self.in_flight = 0
        self.most = 0

    async def rewrite(self, rendered: object, about: object) -> RewriteResult:
        host = getattr(about, "host", "")
        self.seen.append(host)
        self.in_flight += 1
        self.most = max(self.most, self.in_flight)
        try:
            await asyncio.sleep(0.01 * (len(HOSTS) - len(self.seen)))
        finally:
            self.in_flight -= 1
        if host == self.fail:
            raise RuntimeError(f"модель легла на {host}")
        return RewriteResult(zones={"greeting": "Good afternoon to you,"}, tokens_spent=TOKENS)


async def _donors(session: AsyncSession) -> None:
    for dr, host in zip(range(70, 20, -10), HOSTS, strict=True):
        await make_donor(session, host, email=f"info@{host}", dr=dr)


async def _build(session: AsyncSession, rewriter: SlowRewriter) -> BuildReport:
    builder = QueueBuilder(session, rewriter)  # type: ignore[arg-type]
    return await builder.build(BuildRequest(campaign_name="Пачки", niche=("home repair",)))


async def _queued(session: AsyncSession) -> list[str]:
    """Адресаты писем очереди в порядке записи."""
    rows = await session.scalars(
        select(DomainModel.host)
        .join(MessageModel, MessageModel.domain_id == DomainModel.id)
        .order_by(MessageModel.id)
    )
    return list(rows)


@pytest.mark.usefixtures("filled_legal")
class TestPacks:
    async def test_pack_calls_go_together_and_letters_keep_the_selection_order(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "BUILD_CONCURRENCY", 3)
        await _donors(session)
        rewriter = SlowRewriter()

        report = await _build(session, rewriter)

        assert report.prepared == len(HOSTS)
        assert rewriter.most == 3, "пачка из трёх — три вызова модели в пути разом"
        assert rewriter.seen == list(HOSTS)
        assert await _queued(session) == list(HOSTS), "очередь — в порядке отбора, не ответов"

    async def test_one_brings_back_one_call_at_a_time(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "BUILD_CONCURRENCY", 1)
        await _donors(session)
        rewriter = SlowRewriter()

        report = await _build(session, rewriter)

        assert (report.prepared, rewriter.most) == (len(HOSTS), 1)
        assert await _queued(session) == list(HOSTS)

    async def test_failed_call_does_not_lose_its_neighbours(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Токены соседей уже потрачены — их письма в очереди; упавшее письмо
        пишет повтор задачи, и второго письма тем же адресатам не выходит."""
        monkeypatch.setattr(outreach_cfg, "BUILD_CONCURRENCY", 8)
        await _donors(session)

        with pytest.raises(RuntimeError, match=r"модель легла на a\.example\.test"):
            await _build(session, SlowRewriter(fail="a.example.test"))

        assert await _queued(session) == list(HOSTS[1:])
        again = SlowRewriter()
        report = await _build(session, again)
        assert (report.prepared, again.seen) == (1, ["a.example.test"])
        assert sorted(await _queued(session)) == sorted(HOSTS)

    async def test_cap_stops_the_next_pack_not_the_one_in_flight(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Потолок проверяется перед пачкой: начатая доходит до очереди (её вызовы
        уже в пути), следующая не начинается — перебор не больше пачки."""
        monkeypatch.setattr(outreach_cfg, "BUILD_CONCURRENCY", 2)
        monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", TOKENS)
        monkeypatch.setattr(llm_cfg, "RUN_TOKEN_CAP", 0)
        await _donors(session)
        rewriter = SlowRewriter()

        report = await _build(session, rewriter)

        assert report.prepared == 2
        assert rewriter.seen == list(HOSTS[:2])
        assert (report.stopped or "").startswith("потолок расхода на модель за день достигнут")
