"""Доноры с ценами списком — на настоящей базе (`donors/bulk_entry.py`).

Проверяется то, ради чего модуль: каждая строка идёт правилом формы «Завести донора вручную»
и тем же словами отказа (список отказов равен отказам по одному домену); записанное лежит так
же, как от кнопки, с пометкой «загрузка списком» в журнале; повтор, не домен и не цена —
пропуск строки с номером, а не отказ файла; предпросмотр не пишет ничего; запросов к базе не
больше от числа строк.

Домены, которые приводятся к корню, — вида `example.<зона>`, как у тестов ручного ввода:
`*.test` и `.invalid` у списка суффиксов корня нет.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.features.core.domain import (
    AuditAction,
    DonorStatus,
    PriceSource,
    Stage,
    SuppressionReason,
)
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.advertisers import SupplierDonorModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.run import RunCandidateModel
from backend.features.donors import bulk_entry
from backend.features.donors.bulk_entry import UNSUITABLE, Outcome, enter_many
from backend.features.donors.bulk_file import Column, Line
from backend.features.donors.price_refusal import price_refusal, price_refusals
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor, make_sender

WHO = "anna@ours.example.test"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def line(number: int, host: str, price: str = "150", currency: str = "", note: str = "") -> Line:
    return Line(
        number,
        {Column.HOST: host, Column.PRICE: price, Column.CURRENCY: currency, Column.NOTE: note},
    )


async def _donor(session: AsyncSession, host: str) -> DonorModel:
    found = await session.scalar(
        select(DonorModel).join(DomainModel).where(DomainModel.host == host)
    )
    assert found is not None
    return found


async def _journal(session: AsyncSession) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(AuditLogModel.details)
        .where(AuditLogModel.action == AuditAction.PRICE_REVIEWED)
        .order_by(AuditLogModel.id)
    )
    return [row or {} for row in rows.all()]


async def _run(session: AsyncSession) -> int:
    runs = RunRepository(session)
    settings = await runs.create_settings(
        defaults(),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    run = await runs.create_run(
        stage=Stage.DONORS, settings_id=settings.id, keywords=["garden"], country="us"
    )
    return run.id


async def test_new_and_known_donors_are_written_as_by_the_button(session: AsyncSession) -> None:
    await make_donor(session, "example.org")
    lines = [line(2, "https://www.example.com/path", "120", "EUR", "прайс"), line(3, "example.org")]

    report = await enter_many(session, lines, by=WHO, author_id=None, now=NOW, write=True)

    assert [(row.line, row.outcome, row.host, row.crawlable) for row in report.rows] == [
        (2, Outcome.CREATED, "example.com", True),
        (3, Outcome.PRICED, "example.org", True),
    ]
    made = await _donor(session, "example.com")
    assert (made.status, made.review, made.entered_by) == (DonorStatus.SUITABLE, "accepted", WHO)
    assert (made.last_price, made.last_price_currency, made.last_price_note) == (
        Decimal("120.00"),
        "EUR",
        "прайс",
    )
    assert made.last_price_source == PriceSource.MANUAL.value
    known = await _donor(session, "example.org")
    assert (known.last_price, known.last_price_currency) == (Decimal("150.00"), "USD")
    assert [(entry["действие"], entry["как"]) for entry in await _journal(session)] == [
        ("донор заведён вручную, с ценой", "загрузка списком"),
        ("цена указана вручную", "загрузка списком"),
    ]
    assert report.crawl_hosts == ["example.com", "example.org"]


async def test_bad_lines_are_skipped_with_their_numbers_and_the_form_words(
    session: AsyncSession,
) -> None:
    lines = [
        line(2, "example.com"),
        line(3, "www.example.com/other"),
        line(4, ""),
        line(5, "не-адрес"),
        line(6, "example.net", "1,200"),
        line(7, "example.org", "10", "UDS"),
    ]

    report = await enter_many(session, lines, by=WHO, now=NOW, write=True)

    skipped = {row.line: row.reason for row in report.rows if row.outcome is Outcome.SKIPPED}
    assert skipped[3] == "повтор: example.com уже в строке 2"
    assert skipped[4] == "в строке нет домена"
    assert skipped[5] == "«не-адрес» — не домен: впишите адрес сайта, например example.com."
    assert "не цена" in str(skipped[6])
    assert "Валюта «UDS» не знакома" in str(skipped[7])
    assert report.count(Outcome.CREATED) == 1


async def test_a_duplicate_after_a_broken_first_line_is_taken(session: AsyncSession) -> None:
    """Первая строка домена с кривой ценой — не повод отказать второй, верной."""
    report = await enter_many(
        session, [line(2, "example.com", "abc"), line(3, "example.com")], by=WHO, now=NOW
    )

    assert [(row.line, row.outcome) for row in report.rows] == [
        (2, Outcome.SKIPPED),
        (3, Outcome.CREATED),
    ]


async def test_refusals_in_bulk_are_the_refusals_one_by_one(session: AsyncSession) -> None:
    """Список отказов — те же правила и те же слова, что отказ по одному домену."""
    await make_sender(session, "outreach@mail.example.fr")
    stopped = await make_donor(session, "example.com")
    session.add(
        SuppressionModel(domain_id=stopped.id, reason=SuppressionReason.UNSUBSCRIBED, stage=None)
    )
    session.add(SupplierDonorModel(host="example.org", note="размещались"))
    await make_donor(session, "example.net", review="rejected")
    waiting = await make_donor(session, "example.co.uk", review=None)
    session.add(
        RunCandidateModel(run_id=await _run(session), domain_id=waiting.id, status="pending")
    )
    await make_donor(session, "example.de", review=None)
    weak = await _donor(session, "example.de")
    weak.status, weak.reject_reason = DonorStatus.UNSUITABLE, "dr ниже порога"
    await session.flush()
    hosts = [
        "example.com",
        "example.org",
        "example.net",
        "example.co.uk",
        "example.de",
        "example.fr",
        "example.gov",
        "facebook.com",
        "example.es",
    ]
    donors = {
        host: await session.scalar(
            select(DonorModel).join(DomainModel).where(DomainModel.host == host)
        )
        for host in hosts
    }

    bulk = await price_refusals(session, donors)
    one = {host: await price_refusal(session, host, donors[host]) for host in hosts}

    assert bulk == {host: why for host, why in one.items() if why is not None}
    assert set(bulk) == set(hosts) - {"example.es"}


async def test_refused_lines_are_skipped_in_the_same_words(session: AsyncSession) -> None:
    await make_donor(session, "example.net", review="rejected")

    [row] = (await enter_many(session, [line(2, "example.net")], by=WHO, now=NOW)).rows

    assert row.outcome is Outcome.SKIPPED
    assert str(row.reason).startswith("example.net отклонён человеком")


async def test_a_later_word_of_the_donor_wins(session: AsyncSession) -> None:
    await make_donor(session, "example.com")
    donor = await _donor(session, "example.com")
    donor.last_price, donor.last_price_at = Decimal("99.00"), NOW + timedelta(hours=1)

    [row] = (await enter_many(session, [line(2, "example.com")], by=WHO, now=NOW, write=True)).rows

    assert row.outcome is Outcome.SKIPPED
    assert "слово новее этой цены" in str(row.reason)
    assert donor.last_price == Decimal("99.00")


async def test_an_unsuitable_donor_gets_the_price_but_not_the_crawl(session: AsyncSession) -> None:
    await make_donor(session, "example.com")
    (await _donor(session, "example.com")).status = DonorStatus.UNSUITABLE

    [row] = (await enter_many(session, [line(2, "example.com")], by=WHO, now=NOW, write=True)).rows

    assert (row.outcome, row.crawlable, row.reason) == (Outcome.PRICED, False, UNSUITABLE)


async def test_preview_writes_nothing_and_says_the_same(session: AsyncSession) -> None:
    lines = [line(2, "example.com"), line(3, "example.org")]

    preview = await enter_many(session, lines, by=WHO, now=NOW)

    assert int(await session.scalar(select(func.count()).select_from(DomainModel)) or 0) == 0
    assert await _journal(session) == []
    written = await enter_many(session, lines, by=WHO, now=NOW, write=True)
    assert [(row.line, row.outcome) for row in preview.rows] == [
        (row.line, row.outcome) for row in written.rows
    ]


@pytest.mark.parametrize("write", [False, True])
async def test_queries_do_not_grow_with_the_rows(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, write: bool
) -> None:
    """Тысячи строк — не тысячи запросов: проверки множествами, запись пачкой."""
    monkeypatch.setattr(bulk_entry, "normalize_host", lambda raw: raw.strip().lower())
    connection = await session.connection()
    statements: list[str] = []

    def seen(*args: object) -> None:
        statements.append(str(args[2]))

    event.listen(connection.sync_connection, "before_cursor_execute", seen)
    try:
        counts = []
        for size, prefix in ((3, "a"), (30, "b")):
            statements.clear()
            lines = [line(i, f"{prefix}{i}.example") for i in range(1, size + 1)]
            report = await enter_many(session, lines, by=WHO, now=NOW, write=write)
            assert report.count(Outcome.CREATED) == size
            counts.append(len(statements))
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", seen)

    assert counts[0] == counts[1]
