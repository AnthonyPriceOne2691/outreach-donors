"""Качество загрузок: ночной обход одной строкой, прошлые ночи страницами, новое за ночь.

Проверяется то, чем блок может соврать: ночь, посчитанная и строкой дня; обход человека, попавший
в ночную строку; «новый» рекламодатель, которого уже находили; находка ночи, пропавшая после
обхода кнопкой, и чужая находка, приписанная ночи; ноль там, где считать не по чему; прошлая ночь
с числами нынешней. «Новое» сверяется вторым способом — шагом панели.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta

import pytest
from backend.features.core.domain import (
    CrawlOutcome,
    CrawlStatus,
    StopReason,
    SuppressionReason,
    UserRole,
    Verdict,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.advertiser import CandidateMemoryModel
from backend.features.core.models.crawl import CrawlNightModel, CrawlRunModel, OutLinkModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.crawl import gate, load_quality, loads, panel
from backend.features.crawl.night import NIGHT_BY, NightStatus
from backend.features.crawl.panel import Window
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.stage2_rows import crawled, domain_of, promoted, scored

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

#: Московская полночь 10.11 — 21:00 UTC предыдущих суток; круг начинается с неё.
NIGHT = datetime(2026, 11, 9, 21, 0, tzinfo=UTC)
DAY = date(2026, 11, 10)
PERSON = "человек@site.com"

DONOR = "donor-one.example.test"
OTHER = "donor-two.example.test"


def _before(nights: int) -> datetime:
    """Полночь на столько ночей раньше `NIGHT`."""
    return NIGHT - timedelta(days=nights)


async def _run(
    session: AsyncSession,
    host: str,
    moment: datetime,
    *,
    pages: int = 100,
    selling: int | None = 1,
    by: str | None = NIGHT_BY,
) -> CrawlRunModel:
    """Обход, поставленный `by` в минуту `moment`, с числами пересчёта: страниц с продающей
    ссылкой `selling`, ссылок — вдвое больше; `None` — пересчёт этих чисел не клал."""
    run = await crawled(session, host, pages=pages)
    run.created_at = moment
    run.requested_by = by
    run.stats = (
        {} if selling is None else {gate.SELLING_PAGES: selling, gate.SELLING_LINKS: selling * 2}
    )
    await session.flush()
    return run


async def _failed(session: AsyncSession, host: str, moment: datetime) -> CrawlRunModel:
    """Обход круга, который сайт не пустил: донор в ночи есть, страниц нет."""
    run = CrawlRunModel(
        host=host,
        status=CrawlStatus.DONE,
        outcome=CrawlOutcome.BLOCKED,
        stop_reason=StopReason.NO_START,
        pages_opened=0,
        requested_by=NIGHT_BY,
    )
    session.add(run)
    await session.flush()
    run.created_at = moment
    await session.flush()
    return run


async def _link(session: AsyncSession, run: CrawlRunModel, root: str) -> None:
    """Ссылка обхода на домен — история находок: кандидаты хранят только последний обход."""
    session.add(
        OutLinkModel(
            crawl_run_id=run.id,
            page_url=f"https://{run.host}/post",
            url=f"https://{root}/",
            target_host=root,
            target_root=root,
        )
    )
    await session.flush()


async def _found(session: AsyncSession, run: CrawlRunModel, root: str) -> None:
    """Рекламодатель обхода прямой ссылкой: кандидат «куплено» и его ссылка."""
    await scored(session, run, root)
    await _link(session, run, root)


async def test_nights_fold_into_one_row_with_the_last_night(session: AsyncSession) -> None:
    """Те же доноры три ночи подряд — одна строка: итог последней ночи и сколько их было."""
    for back, pages in ((2, 100), (1, 200), (0, 300)):
        await _run(session, DONOR, _before(back) + timedelta(minutes=5), pages=pages, selling=30)
        await _run(session, OTHER, _before(back) + timedelta(minutes=9), pages=pages, selling=0)

    found = await load_quality.page(session, number=1)

    assert found.total == 1
    row = found.rows[0]
    assert (row.kind, row.day, row.load_id) == (load_quality.NIGHT, DAY, None)
    assert row.at == NIGHT + timedelta(minutes=9)
    assert (row.donors, row.host, row.scanned) == (2, None, 2)
    assert (row.pages, row.selling_links, row.selling_pages) == (600, 60, 30)
    assert row.share == pytest.approx(0.05)
    assert row.night is not None
    assert row.night.nights == 3


async def test_a_crawl_put_by_a_person_stays_a_row_of_its_day(session: AsyncSession) -> None:
    """Обход кнопкой — строка дня, и ночь в неё не попадает; строка круга, которую забрал человек
    (обход от его имени в ночной час), — его обход; обход без имени — тоже не ночной."""
    await _run(session, DONOR, NIGHT + timedelta(minutes=5), pages=10)
    await _run(session, OTHER, NIGHT + timedelta(minutes=7), pages=20, by=PERSON)
    await _run(session, "third.example.test", NIGHT + timedelta(hours=15), pages=30, by=PERSON)
    await _run(session, "bare.example.test", _before(1) + timedelta(hours=1), pages=40, by=None)

    found = await load_quality.page(session, number=1)

    assert [(row.kind, row.day, row.donors, row.pages) for row in found.rows] == [
        (load_quality.NO_LOAD, DAY, 2, 50),
        (load_quality.NIGHT, DAY, 1, 10),
        (load_quality.NO_LOAD, DAY - timedelta(days=1), 1, 40),
    ]
    assert found.rows[1].host == DONOR
    day = await load_quality.hosts_of(session, load_id=None, day=DAY, number=1)
    night = await load_quality.hosts_of(session, load_id=None, day=None, night=DAY, number=1)
    assert [row.host for row in day.rows] == [OTHER, "third.example.test"]
    assert [row.host for row in night.rows] == [DONOR]


async def test_the_night_counts_donors_of_loads_and_the_load_keeps_its_last_crawl(
    session: AsyncSession,
) -> None:
    """Круг проходит и доноров партий: ночная строка — вся ночь, строка партии — последний обход
    её донора, а строки дня из ночных обходов не выходит."""
    load = await loads.record(session, kind=loads.BULK, hosts=[DONOR], by=PERSON, file_name="a.csv")
    assert load is not None
    load.created_at = _before(2)
    for back, pages in ((1, 100), (0, 300)):
        await _run(session, DONOR, _before(back) + timedelta(minutes=5), pages=pages)
        await _run(session, OTHER, _before(back) + timedelta(minutes=9), pages=pages)

    found = await load_quality.page(session, number=1)

    assert [(row.kind, row.donors, row.pages) for row in found.rows] == [
        (load_quality.NIGHT, 2, 600),
        (loads.BULK, 1, 300),
    ]
    assert found.total == 2


async def test_past_nights_are_paged_newest_first_without_the_last(session: AsyncSession) -> None:
    """Раскрытие — ночи до последней, каждая своими обходами: донора, которого сайт не пустил,
    ночь считает, а страниц у него нет; ночь без чисел пересчёта пуста, а не ноль."""
    await _run(session, DONOR, NIGHT + timedelta(minutes=5), pages=900, selling=90)
    await _run(session, DONOR, _before(1) + timedelta(minutes=5), pages=400, selling=40)
    await _failed(session, OTHER, _before(1) + timedelta(minutes=9))
    await _run(session, DONOR, _before(2) + timedelta(minutes=5), pages=300, selling=None)
    await _run(session, DONOR, _before(3) + timedelta(minutes=5), pages=200, selling=20)

    first = await load_quality.past_nights(session, number=1, limit=2)
    second = await load_quality.past_nights(session, number=2, limit=2)

    assert first.total == second.total == 3
    assert [row.day for row in (*first.rows, *second.rows)] == [
        DAY - timedelta(days=1),
        DAY - timedelta(days=2),
        DAY - timedelta(days=3),
    ]
    yesterday, bare = first.rows
    assert (yesterday.kind, yesterday.night) == (load_quality.NIGHT, None)
    assert (yesterday.donors, yesterday.scanned, yesterday.pages) == (2, 1, 400)
    assert (yesterday.selling_links, yesterday.share) == (80, pytest.approx(0.1))
    assert (bare.pages, bare.selling_links, bare.share) == (300, None, None)
    assert (second.rows[0].pages, second.rows[0].selling_links) == (200, 40)


async def test_one_night_has_no_past(session: AsyncSession) -> None:
    await _run(session, DONOR, NIGHT + timedelta(minutes=5))

    found = await load_quality.past_nights(session, number=1)

    assert (found.rows, found.total) == ([], 0)


async def test_without_a_round_there_is_no_night_row(session: AsyncSession) -> None:
    await _run(session, DONOR, NIGHT + timedelta(hours=10), by=PERSON)

    found = await load_quality.page(session, number=1)

    assert [row.kind for row in found.rows] == [load_quality.NO_LOAD]
    assert found.rows[0].night is None
    assert (await load_quality.past_nights(session, number=1)).total == 0


async def test_fresh_is_what_was_never_met_before_the_night(session: AsyncSession) -> None:
    """Новый — домен «куплено», которого до ночи не было ни в ссылках обходов, ни среди
    рекламодателей, ни в памяти пары. Остальное — повтор, сколько бы ночей его ни находили."""
    before = await _run(session, DONOR, _before(1) + timedelta(minutes=5))
    await _link(session, before, "linked.example.test")
    run = await _run(session, DONOR, NIGHT + timedelta(minutes=5))
    other = await _run(session, OTHER, NIGHT + timedelta(minutes=9))
    for root in ("fresh.example.test", "linked.example.test", "both.example.test"):
        await _found(session, run, root)
    # Домен двух доноров одной ночи — один новый рекламодатель, а не два.
    await _found(session, other, "both.example.test")
    # Уже рекламодатель: перевод завёл его раньше ночи.
    await _found(session, run, "known.example.test")
    (await promoted(session, "known.example.test", donor_host=DONOR)).created_at = _before(1)
    # Найден через сеть партнёрок: ссылки называют домен сети, а память пары его помнит.
    await scored(session, run, "network.example.test")
    session.add(
        CandidateMemoryModel(
            donor_host=DONOR,
            target_root="network.example.test",
            dr=40,
            dr_checked_at=_before(1),
            created_at=_before(1),
        )
    )
    # Кому не пишем и о ком скоринг не решил — не рекламодатели: в новых их нет.
    await scored(session, run, "refused.example.test", confirmed=False)
    await scored(session, run, "maybe.example.test", verdict=Verdict.PENDING)
    await session.flush()

    night = (await load_quality.page(session, number=1)).rows[0].night

    assert night is not None
    assert night.fresh == 2


async def test_a_pair_remembered_this_night_is_still_fresh(session: AsyncSession) -> None:
    """Память пары заводит пересчёт той же ночи, спросив DR: «раньше ночи» она не говорит."""
    run = await _run(session, DONOR, NIGHT + timedelta(minutes=5))
    await scored(session, run, "network.example.test")
    session.add(
        CandidateMemoryModel(
            donor_host=DONOR,
            target_root="network.example.test",
            dr=40,
            dr_checked_at=NIGHT + timedelta(minutes=30),
            created_at=NIGHT + timedelta(minutes=30),
        )
    )
    await session.flush()

    night = (await load_quality.page(session, number=1)).rows[0].night

    assert night is not None
    assert night.fresh == 1


async def test_a_supplier_gives_nothing_fresh(session: AsyncSession) -> None:
    """Рекламодателей поставщика не трогаем: в новых их нет, как и в шагах панели."""
    run = await _run(session, DONOR, NIGHT + timedelta(minutes=5))
    await _found(session, run, "theirs.example.test")
    domain = await domain_of(session, DONOR)
    session.add(SuppressionModel(domain_id=domain.id, reason=SuppressionReason.SUPPLIER))
    await session.flush()

    night = (await load_quality.page(session, number=1)).rows[0].night

    assert night is not None
    assert night.fresh == 0


async def _night_row(session: AsyncSession) -> load_quality.QualityRow:
    """Ночная строка первой страницы — где бы она ни стояла среди строк дня."""
    found = await load_quality.page(session, number=1)
    return next(row for row in found.rows if row.kind == load_quality.NIGHT)


async def test_a_later_crawl_does_not_take_away_what_the_night_found(
    session: AsyncSession,
) -> None:
    """Донора обошли кнопкой после ночи: его кандидаты уже у нового обхода. Находка ночи — домен,
    что стоял в ссылках ночного обхода: иначе число ночи убывало бы днём с каждым «Переобойти».
    Домен, который нашёл только обход кнопкой, ночи не приписывается."""
    night = await _run(session, DONOR, NIGHT + timedelta(minutes=5), pages=300, selling=30)
    await _link(session, night, "kept.example.test")
    other = await _run(session, OTHER, NIGHT + timedelta(minutes=9))
    await _found(session, other, "theirs.example.test")
    later = await _run(session, DONOR, NIGHT + timedelta(hours=12), by=PERSON)
    await _found(session, later, "kept.example.test")
    await _found(session, later, "late.example.test")

    row = await _night_row(session)

    assert row.night is not None
    assert row.night.fresh == 2
    # Числа ночи лежат на её обходах и от обхода кнопкой не меняются.
    assert (row.donors, row.pages) == (2, 400)


async def test_fresh_is_empty_not_zero_until_the_night_is_judged(session: AsyncSession) -> None:
    """Пересчёт до ночного обхода не дошёл: кандидаты донора — ещё вчерашнего обхода. «Новых
    ноль» сказало бы «искали и не нашли»; числа нет, а страницы ночи уже есть."""
    before = await _run(session, DONOR, _before(1) + timedelta(minutes=5))
    await _found(session, before, "known.example.test")
    await _run(session, DONOR, NIGHT + timedelta(minutes=5), pages=300, selling=None)

    row = await _night_row(session)

    assert row.night is not None
    assert row.night.fresh is None
    assert (row.pages, row.selling_links) == (300, None)


async def test_a_night_that_was_not_let_in_did_not_look(session: AsyncSession) -> None:
    """Ночью сайт не пустил, днём донора обошли кнопкой: у ночи пригодного обхода нет, и «новых
    нет» сказало бы «искали и не нашли». Находки дня — обхода кнопкой, а у ночи числа нет."""
    await _failed(session, DONOR, NIGHT + timedelta(minutes=5))
    later = await _run(session, DONOR, NIGHT + timedelta(hours=12), by=PERSON)
    await _found(session, later, "late.example.test")

    row = await _night_row(session)

    assert row.night is not None
    assert row.night.fresh is None
    assert (row.donors, row.scanned, row.pages) == (1, 0, 0)


async def test_fresh_agrees_with_the_step_of_the_panel(session: AsyncSession) -> None:
    """Второй способ: «новых» шага панели за выборку с полуночи — то же число, пока с полуночи
    шли только обходы круга. Два «новых» на одном экране не расходятся."""
    before = await _run(session, DONOR, _before(1) + timedelta(minutes=5))
    await _link(session, before, "linked.example.test")
    run = await _run(session, DONOR, NIGHT + timedelta(minutes=5))
    for root in ("fresh.example.test", "another.example.test", "linked.example.test"):
        await _found(session, run, root)

    night = (await load_quality.page(session, number=1)).rows[0].night
    step = await panel.funnel(session, Window(since=NIGHT))

    assert night is not None
    assert night.fresh == step.fresh == 2
    assert step.repeat == 1


@pytest.mark.parametrize(
    ("status", "running"),
    [(NightStatus.RUNNING, True), (NightStatus.DONE, False), (None, False)],
)
async def test_a_round_that_still_goes_is_said(
    session: AsyncSession, status: NightStatus | None, running: bool
) -> None:
    """Круг ночи ещё идёт — итог строки не окончательный, и строка об этом знает. Строки круга
    нет (обходы от его имени завели иначе) — не идёт."""
    await _run(session, DONOR, NIGHT + timedelta(minutes=5))
    if status is not None:
        session.add(CrawlNightModel(night=DAY, status=status.value, started_at=NIGHT))
    # Вчерашний круг идти уже не может, но его строка есть: читается строка этой ночи.
    session.add(CrawlNightModel(night=DAY - timedelta(days=1), status=NightStatus.RUNNING.value))
    await session.flush()

    night = (await load_quality.page(session, number=1)).rows[0].night

    assert night is not None
    assert night.running is running


# --- маршруты ---


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("ночь@site.com", role=UserRole.OPERATOR)
    return await sign_in("ночь@site.com")


async def test_the_night_row_and_its_past_over_the_api(
    client: AsyncClient, token: str, session: AsyncSession
) -> None:
    # Обходы — по порядку ночей: «более поздний обход» у пересчёта и у панели — по номеру.
    oldest, *_ = [
        await _run(session, DONOR, _before(back) + timedelta(minutes=5), pages=100 + back)
        for back in (2, 1, 0)
    ]
    await _found(session, oldest, "old.example.test")
    await session.commit()

    quality = await client.get("/api/advertisers/panel/quality", headers=bearer(token))
    nights = await client.get("/api/advertisers/panel/quality/nights", headers=bearer(token))
    stranger = await client.get("/api/advertisers/panel/quality/nights")

    assert quality.status_code == nights.status_code == 200
    row = quality.json()["rows"][0]
    assert (row["kind"], row["day"], row["pages"]) == ("night", "2026-11-10", 100)
    # Кандидаты — у обхода двухдневной давности: пересчёт до последней ночи не дошёл, «новых» пусто.
    assert row["night"] == {"nights": 3, "running": False, "fresh": None}
    body = nights.json()
    assert (body["total"], body["page"], body["limit"]) == (2, 1, load_quality.NIGHTS_PAGE_SIZE)
    assert [(line["day"], line["pages"], line["night"]) for line in body["rows"]] == [
        ("2026-11-09", 101, None),
        ("2026-11-08", 102, None),
    ]
    assert stranger.status_code == 403


async def test_hosts_of_a_night_over_the_api(
    client: AsyncClient, token: str, session: AsyncSession
) -> None:
    await _run(session, DONOR, NIGHT + timedelta(minutes=5))
    await _run(session, OTHER, NIGHT + timedelta(hours=12), by=PERSON)
    await session.commit()
    path = "/api/advertisers/panel/quality/hosts"

    night = await client.get(f"{path}?night=2026-11-10", headers=bearer(token))
    both = await client.get(f"{path}?night=2026-11-10&day=2026-11-10", headers=bearer(token))
    loaded = await client.get(f"{path}?night=2026-11-10&load=1", headers=bearer(token))

    assert [row["host"] for row in night.json()["rows"]] == [DONOR]
    assert both.status_code == loaded.status_code == 422
    assert "что-то одно" in both.json()["detail"]
