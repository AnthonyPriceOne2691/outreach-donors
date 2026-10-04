"""Потолок расхода на модель — по журналу расхода, как предел у Ahrefs.

BACKLOG 28.09.2026: у модели не было предела трат — ошибка в промпте или
зацикленный повтор платили бы без границы. Решение Anthony 04.10.2026: два
потолка в токенах (за день и за прогон), при превышении — остановка словами.
Проверяется на настоящей базе: считает тот же журнал, что и экран расхода.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.api import errors as api_errors
from backend.cli import main as cli_main
from backend.config import llm as llm_cfg
from backend.features.core import usage
from backend.features.core.domain import RunStatus, Stage
from backend.features.core.models.run import RunModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.donors.verdict import Thresholds
from backend.features.replies.pipeline import Inbox, Parser
from backend.features.runs import failures
from backend.features.runs.pipeline import JUDGE_OPERATION
from backend.features.runs.repository import RunRepository
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_letters_advertisers import FakeExtractor
from tests.test_letters_queue import FakeRewriter, _build, make_donor
from tests.test_replies_inbox import NOW, inbound_secret, reply_from, sent

__all__ = ["inbound_secret", "sent"]  # фикстуры приёма — отсюда их видит pytest


@pytest.fixture
def caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 0)
    monkeypatch.setattr(llm_cfg, "RUN_TOKEN_CAP", 0)


async def _run(session: AsyncSession) -> int:
    """Настоящий прогон: у строки расхода внешний ключ на прогон."""
    settings = await RunRepository(session).create_settings(
        Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    run = RunModel(
        stage=Stage.DONORS,
        settings_id=settings.id,
        status=RunStatus.DONE,
        keywords=["a"],
        country="us",
    )
    session.add(run)
    await session.flush()
    return run.id


async def _spend(
    session: AsyncSession, tokens: int, *, run_id: int | None = None, days_ago: int = 0
) -> None:
    entry = usage.record(session, operation=JUDGE_OPERATION, units=tokens, run_id=run_id)
    await session.flush()
    if days_ago:
        entry.created_at = datetime.now(UTC) - timedelta(days=days_ago)
        await session.flush()


@pytest.mark.usefixtures("caps")
class TestLedger:
    async def test_counts_only_the_model_and_only_since(self, session: AsyncSession) -> None:
        await _spend(session, 100)
        await _spend(session, 50, days_ago=2)
        usage.record(session, operation="letter_send", units=1)  # почта, не модель
        await session.flush()

        assert await usage.llm_tokens_spent(session) == 150
        since = datetime.now(UTC) - timedelta(hours=1)
        assert await usage.llm_tokens_spent(session, since=since) == 100

    async def test_run_counts_only_its_own_rows(self, session: AsyncSession) -> None:
        run_id = await _run(session)
        await _spend(session, 30, run_id=run_id)
        await _spend(session, 70)
        assert await usage.llm_tokens_spent(session, run_id=run_id) == 30


@pytest.mark.usefixtures("caps")
class TestCap:
    async def test_zero_means_no_cap(self, session: AsyncSession) -> None:
        await _spend(session, 10_000)
        await usage.ensure_llm_within_cap(session, run_id=await _run(session))

    async def test_daily_cap_stops_with_numbers(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 1_000)
        await _spend(session, 600)
        await usage.ensure_llm_within_cap(session)
        await _spend(session, 400)

        with pytest.raises(LlmCapExceededError, match=r"1000 из 1000.*LLM_DAILY_TOKEN_CAP"):
            await usage.ensure_llm_within_cap(session)

    async def test_yesterday_does_not_count_today(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 1_000)
        await _spend(session, 5_000, days_ago=1)
        await usage.ensure_llm_within_cap(session)

    async def test_run_cap_is_about_this_run_only(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(llm_cfg, "RUN_TOKEN_CAP", 100)
        first, second = await _run(session), await _run(session)
        await _spend(session, 100, run_id=first)
        await usage.ensure_llm_within_cap(session, run_id=second)
        await usage.ensure_llm_within_cap(session)  # без прогона потолок прогона не судит
        with pytest.raises(LlmCapExceededError, match=rf"прогон №{first} "):
            await usage.ensure_llm_within_cap(session, run_id=first)


class TestWhereItStops:
    def test_run_treats_it_as_a_refusal_not_a_crash(self) -> None:
        """Прогон останавливается причиной, как при нехватке юнитов, а не
        уходит на повтор задачей."""
        assert failures.is_permanent(LlmCapExceededError("потолок"))

    def test_api_answers_429(self) -> None:
        assert api_errors.STATUSES[LlmCapExceededError] == status.HTTP_429_TOO_MANY_REQUESTS

    def test_cli_exit_code_is_the_cap_code(self) -> None:
        codes = {kind: code for kind, code, _ in cli_main._FAILURES}
        assert codes[LlmCapExceededError] == cli_main.EXIT_CAP_EXCEEDED

    @pytest.mark.usefixtures("caps", "filled_legal")
    async def test_letters_build_stops_and_reports(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Сборка писем: подготовленное остаётся, остальное — следующей сборкой."""
        await make_donor(session, "a.example.test", email="info@a.example.test")
        await make_donor(session, "b.example.test", email="info@b.example.test")
        monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 1)
        await _spend(session, 1)
        rewriter = FakeRewriter()

        report = await _build(session, rewriter=rewriter)

        assert report.prepared == 0  # type: ignore[attr-defined]
        assert "LLM_DAILY_TOKEN_CAP" in (report.stopped or "")  # type: ignore[attr-defined]
        assert rewriter.seen == []

    @pytest.mark.usefixtures("caps")
    async def test_reply_parse_raises_for_the_queue_to_retry(
        self, session: AsyncSession, sent: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Разбор ответа не судит без модели и не теряет ответ: исключение
        уходит задаче, очередь повторит позже."""
        got = await Inbox(session, now=NOW).accept(reply_from(sent, "Our price is $90."))  # type: ignore[arg-type]
        assert got.reply_id is not None
        monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 1)
        await _spend(session, 1)
        extractor = FakeExtractor()

        with pytest.raises(LlmCapExceededError):
            await Parser(session, extractor, now=NOW).parse(got.reply_id)  # type: ignore[arg-type]
