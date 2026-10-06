"""Консоль рекламодателей: пересчёт, DR, решение, перевод, стоп-лист — словами.

Команды открывают свою базу, поэтому здесь настоящие фиксации и чистка
после (`committed_sessions`). Проверяется то, что видит человек
в терминале, и то, что остаётся в базе: «DR > 80» спрошен один раз,
а его цена лежит в журнале расхода.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

import pytest
from backend.cli import advertisers
from backend.config import ahrefs as ahrefs_cfg
from backend.config import storage
from backend.features.ahrefs.client import Response
from backend.features.ahrefs.units import UnitsCost
from backend.features.core.domain import CrawlOutcome, StopReason, Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.crawl.links import OutLink
from backend.features.crawl.repository import save_crawl
from backend.features.crawl.walk import CrawlReport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import TEST_DSN
from tests.test_send_race import committed_sessions

DONOR = "donor-cli.example.test"


@pytest.fixture(autouse=True)
def _test_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)


def _judge(**overrides: Any) -> argparse.Namespace:
    values: dict[str, Any] = {"run": None, "verdict": None, "limit": 30, "no_dr": True}
    values.update(overrides)
    return argparse.Namespace(**values)


def _link(root: str, *, sponsored: bool) -> OutLink:
    anchor = "Acme" if sponsored else "a long editorial mention of the source"
    return OutLink(
        page_url=f"https://{DONOR}/post/1",
        url=f"https://{root}/",
        target_host=root,
        target_root=root,
        anchor=anchor,
        anchor_key=anchor.lower(),
        nofollow=False,
        sponsored=sponsored,
        ugc=False,
    )


async def _crawl(factory: async_sessionmaker[AsyncSession]) -> int:
    links = [
        _link("marketplace.example", sponsored=True),
        _link("small-brand.example", sponsored=True),
        _link("plain-source.example", sponsored=False),
    ]
    async with factory() as session:
        run = await save_crawl(
            session,
            CrawlReport(
                host=DONOR,
                outcome=CrawlOutcome.OK,
                stop_reason=StopReason.EXHAUSTED,
                pages=[f"https://{DONOR}/post/1"],
                links=links,
                articles=1,
            ),
        )
        await session.commit()
        return run.id


class _FakeAhrefs:
    """Клиент провайдера: отвечает DR и сообщает расход, как настоящий."""

    asked: ClassVar[list[list[str]]] = []

    def __init__(self, *, on_usage: Any = None) -> None:
        self.on_usage = on_usage

    async def batch_metrics(self, hosts: Sequence[str], select: Sequence[str]) -> Response:
        type(self).asked.append(list(hosts))
        cost = UnitsCost(actual=50, estimated=50, per_row=2)
        if self.on_usage is not None:
            self.on_usage("batch_metrics", cost)
        known = {"marketplace.example": 96, "small-brand.example": 23}
        rows = [{"url": f"https://{h}/", "domain_rating": known[h]} for h in hosts if h in known]
        return Response(rows=rows, cost=cost)

    async def aclose(self) -> None:
        return None


def test_commands_and_their_arguments() -> None:
    parser = argparse.ArgumentParser()
    advertisers.add_parser(parser.add_subparsers(dest="command"))

    judge = parser.parse_args(["advertisers", "--run", "3", "--no-dr"])
    promote = parser.parse_args(["advertisers-promote", "--contacts", "--no-paid"])
    decide = parser.parse_args(
        ["advertiser-decide", "--run", "3", "--domain", "a.com", "--by", "op@t.test", "--no"]
    )

    assert (judge.run, judge.no_dr, judge.limit) == (3, True, 30)
    assert (promote.contacts, promote.no_paid, promote.limit) == (True, True, 100)
    assert (decide.yes, decide.no) == (False, True)


async def test_no_crawls_says_how_to_start(capsys: pytest.CaptureFixture[str]) -> None:
    async with committed_sessions():
        code = await advertisers.cmd_advertisers(_judge())

    assert code == advertisers.EXIT_NOT_FOUND
    assert "сначала `outreach crawl" in capsys.readouterr().out


async def test_without_dr_it_is_said_not_hidden(capsys: pytest.CaptureFixture[str]) -> None:
    async with committed_sessions() as factory:
        await _crawl(factory)
        code = await advertisers.cmd_advertisers(_judge(limit=1))

    out = capsys.readouterr().out
    assert code == 0
    assert "DR не спрашиваем (--no-dr)" in out
    assert "кандидатов: 3" in out
    assert "DR не проверен" in out
    assert "… ещё 2" in out


async def test_without_a_key_it_is_said(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ahrefs_cfg, "API_KEY", "")
    async with committed_sessions() as factory:
        await _crawl(factory)
        await advertisers.cmd_advertisers(_judge(no_dr=False))

    assert "Ключа Ahrefs нет" in capsys.readouterr().out


async def test_big_site_is_asked_once_and_the_spend_is_written(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ahrefs_cfg, "API_KEY", "test-key")
    monkeypatch.setattr(advertisers, "AhrefsClient", _FakeAhrefs)
    _FakeAhrefs.asked = []
    async with committed_sessions() as factory:
        run_id = await _crawl(factory)
        await advertisers.cmd_advertisers(_judge(no_dr=False))
        await advertisers.cmd_advertisers(_judge(no_dr=False, run=run_id, verdict="blocked"))

        async with factory() as session:
            rows = (await session.execute(select(CandidateModel))).scalars().all()
            spent = (await session.execute(select(UsageRecordModel))).scalars().all()

    verdicts = {row.target_root: row.verdict for row in rows}
    assert _FakeAhrefs.asked == [["marketplace.example", "small-brand.example"]]
    assert verdicts["marketplace.example"] is Verdict.BLOCKED
    assert verdicts["small-brand.example"] is Verdict.BOUGHT
    assert [(row.operation, row.units) for row in spent] == [("batch_metrics", 50)]
    assert "кому не пишем: DR 96 > 80" in capsys.readouterr().out


async def test_decision_and_unknown_candidate(capsys: pytest.CaptureFixture[str]) -> None:
    async with committed_sessions() as factory:
        run_id = await _crawl(factory)
        await advertisers.cmd_advertisers(_judge())
        yes = argparse.Namespace(
            run=run_id, domain="Small-Brand.example", by="op@t.test", yes=True, no=False
        )
        missing = argparse.Namespace(
            run=run_id, domain="nobody.example", by="op@t.test", yes=False, no=True
        )

        decided = await advertisers.cmd_advertiser_decide(yes)
        not_found = await advertisers.cmd_advertiser_decide(missing)

    out = capsys.readouterr().out
    assert (decided, not_found) == (0, advertisers.EXIT_NOT_FOUND)
    assert "small-brand.example: подтверждён (op@t.test)" in out
    assert "Кандидата nobody.example" in out


async def test_promote_without_contacts_says_how_to_find_them(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with committed_sessions() as factory:
        await _crawl(factory)
        await advertisers.cmd_advertisers(_judge())
        code = await advertisers.cmd_advertisers_promote(
            argparse.Namespace(contacts=False, limit=10, no_paid=True)
        )

    out = capsys.readouterr().out
    assert code == 0
    assert "Перевод кандидатов в рекламодателей" in out
    assert "Без адреса: 2" in out
    assert "Искать адреса" in out


async def test_suppliers_import(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    listing = tmp_path / "suppliers.txt"
    empty = tmp_path / "empty.txt"
    listing.write_text("# доноры, где размещались\nwww.One.example # партнёр\ntwo.example\n\n")
    empty.write_text("# только шапка\n")

    async with committed_sessions():
        missing = await advertisers.cmd_suppliers_import(
            argparse.Namespace(path=tmp_path / "нет.txt", by="op@t.test")
        )
        blank = await advertisers.cmd_suppliers_import(argparse.Namespace(path=empty, by="op"))
        first = await advertisers.cmd_suppliers_import(argparse.Namespace(path=listing, by="op"))
        again = await advertisers.cmd_suppliers_import(argparse.Namespace(path=listing, by="op"))

    out = capsys.readouterr().out
    assert (missing, blank, first, again) == (
        advertisers.EXIT_NOT_FOUND,
        advertisers.EXIT_NOT_FOUND,
        0,
        0,
    )
    assert "Добавлено: 2, уже было: 0. Всего в списке: 2." in out
    assert "Добавлено: 0, уже было: 2." in out
