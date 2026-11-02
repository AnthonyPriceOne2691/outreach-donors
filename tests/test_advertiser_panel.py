"""Панель рекламодателей: шаги выборки, партии, качество загрузок.

Проверяется то, чем панель может соврать: число шага не из этой выборки (вчерашний обход
в «сегодня»), шаги, которые не вложены, новый и повторный домен, донор без обхода и обход без
страниц, письмо, которое ушло, но не дошло, партия, которой нет. Сумма шагов сверяется вторым
способом — прямым запросом, а не той же функцией.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.core.domain import (
    CrawlOutcome,
    CrawlStatus,
    MessageStatus,
    StopReason,
    SuppressionReason,
    UserRole,
    Verdict,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import (
    CrawlBacklogModel,
    CrawlRunModel,
    DonorLoadHostModel,
    DonorLoadModel,
    OutLinkModel,
)
from backend.features.core.models.ops import SuppressionModel
from backend.features.crawl import gate, load_quality, loads, panel
from backend.features.crawl.acceptance import Tally
from backend.features.crawl.links import OutLink
from backend.features.crawl.panel import DonorSteps, Range, Window
from backend.features.crawl.scoring import Candidate
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.migration_helpers import load_migration
from tests.stage2_rows import addressed, crawled, domain_of, lettered, promoted, scored

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
#: Московская полночь 02.11 — 21:00 UTC предыдущих суток.
TODAY = datetime(2026, 11, 1, 21, 0, tzinfo=UTC)
YESTERDAY = TODAY - timedelta(hours=3)

DONOR = "donor-one.example.test"
OTHER = "donor-two.example.test"


async def _at(session: AsyncSession, run: CrawlRunModel, moment: datetime) -> CrawlRunModel:
    """Обход, заказанный в минуту `moment`: умолчание базы — время транзакции теста."""
    run.created_at = moment
    await session.flush()
    return run


async def _pending(session: AsyncSession, host: str, moment: datetime) -> CrawlRunModel:
    """Обход ждёт в очереди обходов: страниц нет, исхода нет."""
    run = CrawlRunModel(host=host, status=CrawlStatus.QUEUED)
    session.add(run)
    await session.flush()
    return await _at(session, run, moment)


async def _refused(session: AsyncSession, host: str, moment: datetime) -> CrawlRunModel:
    """Обход кончился, не открыв ни страницы: сайт закрылся с первой."""
    run = CrawlRunModel(
        host=host,
        status=CrawlStatus.DONE,
        outcome=CrawlOutcome.BLOCKED,
        stop_reason=StopReason.NO_START,
        pages_opened=0,
    )
    session.add(run)
    await session.flush()
    return await _at(session, run, moment)


async def _link(session: AsyncSession, run: CrawlRunModel, root: str) -> None:
    """Ссылка обхода на домен — след в истории: кандидаты хранят только последний обход."""
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


async def _load(
    session: AsyncSession, hosts: list[str], moment: datetime, *, kind: str = loads.BULK
) -> DonorLoadModel:
    load = await loads.record(
        session, kind=kind, hosts=hosts, by="человек@site.com", file_name="party.xlsx", crawl=True
    )
    assert load is not None
    load.created_at = moment
    await session.flush()
    return load


@pytest.mark.parametrize(
    ("chosen", "since"),
    [
        (Range.TODAY, TODAY),
        (Range.WEEK, TODAY - timedelta(days=6)),
        (Range.ALL, None),
    ],
)
async def test_the_window_is_counted_by_moscow_days(
    session: AsyncSession, chosen: Range, since: datetime | None
) -> None:
    """Сутки — по Москве: в полдень по UTC 02.11 «сегодня» началось вчера в 21:00 по UTC."""
    window = await panel.window_of(session, chosen, None, now=NOW)

    assert window == Window(since=since)


async def test_a_load_that_is_not_there_is_refused_with_words(session: AsyncSession) -> None:
    with pytest.raises(panel.UnknownLoadError, match="Партии №7 нет"):
        await panel.window_of(session, Range.LOAD, 7, now=NOW)


async def test_donors_of_the_window_are_each_in_one_state(session: AsyncSession) -> None:
    """Открыл страницы, ждёт, идёт, не открылся — итог у донора один, и сумма сходится."""
    await _at(session, await crawled(session, "opened.example.test"), TODAY)
    await _pending(session, "queued.example.test", TODAY)
    await _refused(session, "closed.example.test", TODAY)
    session.add(CrawlBacklogModel(host="backlog.example.test", created_at=TODAY))
    # Вчерашний обход в «сегодня» не считается, а второй обход того же донора — не второй донор.
    await _at(session, await crawled(session, "old.example.test"), YESTERDAY)
    await _refused(session, "opened.example.test", TODAY + timedelta(hours=1))
    await session.flush()

    steps = await panel.donor_steps(session, Window(since=TODAY))

    assert steps == DonorSteps(added=4, started=1, running=1, waiting=1, refused=1)
    assert steps.added == steps.started + steps.running + steps.waiting + steps.refused


async def test_a_donor_in_the_backlog_with_a_crawl_is_not_counted_twice(
    session: AsyncSession,
) -> None:
    await _at(session, await crawled(session, DONOR), TODAY)
    session.add(CrawlBacklogModel(host=DONOR, created_at=TODAY))
    await session.flush()

    steps = await panel.donor_steps(session, Window(since=TODAY))

    assert (steps.added, steps.started, steps.waiting) == (1, 1, 0)


async def test_steps_of_advertisers_are_nested_and_from_the_window(session: AsyncSession) -> None:
    """Домен двух доноров — один; не переведённый адреса не получает; письмо считается у домена
    с адресом; «ушло» и «дошло» — разные шаги; вчерашний обход сюда не попадает."""
    run = await _at(session, await crawled(session, DONOR), TODAY)
    second = await _at(session, await crawled(session, OTHER), TODAY)
    old = await _at(session, await crawled(session, "old.example.test"), YESTERDAY)
    await scored(session, run, "reached.example.test")
    await scored(session, second, "reached.example.test")
    await scored(session, run, "sent.example.test")
    await scored(session, run, "addressed.example.test")
    await scored(session, run, "promoted.example.test")
    await scored(session, run, "found.example.test")
    await scored(session, run, "maybe.example.test", verdict=Verdict.PENDING)
    await scored(session, run, "refused.example.test", confirmed=False)
    await scored(session, old, "yesterday.example.test")
    for host in (
        "reached.example.test",
        "sent.example.test",
        "addressed.example.test",
        "promoted.example.test",
    ):
        await promoted(session, host, donor_host=DONOR)
    await lettered(session, "reached.example.test", MessageStatus.DELIVERED)
    await lettered(session, "sent.example.test", MessageStatus.SENT)
    await addressed(session, "addressed.example.test")
    # Адрес без перевода шага не даёт: письма такому домену не будет.
    await addressed(session, "found.example.test")

    found = await panel.funnel(session, Window(since=TODAY))

    assert found.tally == Tally(found=5, promoted=4, with_address=3, sent=2)
    assert found.delivered == 1
    assert found.fresh + found.repeat == found.tally.found
    # Второй способ: те же домены прямым запросом, без правил приёмки.
    direct = await session.scalar(
        select(func.count(func.distinct(CandidateModel.target_root)))
        .join(CrawlRunModel, CrawlRunModel.id == CandidateModel.crawl_run_id)
        .where(
            CrawlRunModel.created_at >= TODAY,
            CandidateModel.verdict == Verdict.BOUGHT,
            CandidateModel.confirmed.is_not(False),
        )
    )
    assert direct == found.tally.found


async def test_a_letter_that_went_but_did_not_reach_is_not_delivered(session: AsyncSession) -> None:
    run = await _at(session, await crawled(session, DONOR), TODAY)
    for host, status in (
        ("bounced.example.test", MessageStatus.BOUNCED),
        ("queued.example.test", MessageStatus.QUEUED),
    ):
        await scored(session, run, host)
        await promoted(session, host, donor_host=DONOR)
        await lettered(session, host, status)

    found = await panel.funnel(session, Window(since=TODAY))

    # Отказ — тоже ушедшее письмо (`GONE_STATUSES`), но не дошедшее; собранное — ещё не ушло.
    assert (found.tally.with_address, found.tally.sent, found.delivered) == (2, 1, 0)


async def test_a_domain_seen_before_the_window_is_a_repeat(session: AsyncSession) -> None:
    """Новый — не встречался раньше выборки; ссылка на него в прошлом обходе делает его
    повторным, как и строка рекламодателя: сеть партнёрок в ссылках лежит доменом сети."""
    old = await _at(session, await crawled(session, OTHER), YESTERDAY)
    await _link(session, old, "seen.example.test")
    run = await _at(session, await crawled(session, DONOR), TODAY)
    await scored(session, run, "seen.example.test")
    await scored(session, run, "fresh.example.test")
    await scored(session, run, "known.example.test")
    known = await promoted(session, "known.example.test", donor_host=OTHER)
    known.created_at = YESTERDAY
    # Сосед по выборке прошлым не считается: домен сегодняшнего обхода остаётся новым.
    today = await _at(session, await crawled(session, "third.example.test"), TODAY)
    await _link(session, today, "fresh.example.test")
    await session.flush()

    found = await panel.funnel(session, Window(since=TODAY))

    assert (found.tally.found, found.fresh, found.repeat) == (3, 1, 2)


async def test_all_time_has_nothing_before_it(session: AsyncSession) -> None:
    run = await _at(session, await crawled(session, DONOR), TODAY)
    await scored(session, run, "one.example.test")
    await _link(session, run, "one.example.test")

    found = await panel.funnel(session, Window())

    assert (found.fresh, found.repeat) == (1, 0)


async def test_a_supplier_is_not_counted(session: AsyncSession) -> None:
    """Рекламодателей поставщика не трогаем: в сумме их нет, как и в приёмке."""
    run = await _at(session, await crawled(session, DONOR), TODAY)
    await scored(session, run, "theirs.example.test")
    domain = await domain_of(session, DONOR)
    session.add(SuppressionModel(domain_id=domain.id, reason=SuppressionReason.SUPPLIER))
    await session.flush()

    found = await panel.funnel(session, Window(since=TODAY))

    assert found.tally == Tally()


async def test_a_load_counts_its_hosts_and_their_crawls_only(session: AsyncSession) -> None:
    """Партия: пустой домен без обхода — виден, чужой обход и обход до загрузки — нет."""
    load = await _load(
        session,
        [DONOR, OTHER, "idle.example.test", "wait.example.test"],
        TODAY,
    )
    await _at(session, await crawled(session, OTHER), YESTERDAY)
    run = await _at(session, await crawled(session, DONOR), TODAY + timedelta(hours=1))
    await _at(session, await crawled(session, "stranger.example.test"), TODAY + timedelta(hours=1))
    session.add(CrawlBacklogModel(host="wait.example.test", created_at=TODAY))
    await scored(session, run, "advertiser.example.test")
    await session.flush()

    found = await panel.funnel(session, Window(since=load.created_at, load=load))

    assert found.donors == DonorSteps(added=4, started=1, waiting=1, idle=2)
    assert found.tally.found == 1


async def test_an_empty_window_is_all_zeros(session: AsyncSession) -> None:
    found = await panel.funnel(session, Window(since=TODAY))

    assert found.donors == DonorSteps()
    assert (found.tally, found.fresh, found.repeat, found.delivered) == (Tally(), 0, 0, 0)


async def test_a_load_is_written_once_per_host_and_nothing_empty(session: AsyncSession) -> None:
    assert await loads.record(session, kind=loads.BULK, hosts=[], by=None) is None
    load = await loads.record(
        session, kind=loads.BULK, hosts=[DONOR, DONOR, OTHER], by=None, file_name="x" * 400
    )

    assert load is not None
    assert len(load.file_name or "") == loads.FILE_NAME_MAX
    hosts = await session.scalars(
        select(DonorLoadHostModel.host).where(DonorLoadHostModel.load_id == load.id)
    )
    assert sorted(hosts.all()) == sorted([DONOR, OTHER])
    assert await loads.record(session, kind=loads.MANUAL, hosts=[DONOR], by=None) is not None
    assert [(row.kind, hosts_count) for row, hosts_count in await loads.recent(session, limit=5)][
        0
    ] == (loads.MANUAL, 1)


def _outlink(page: str, root: str) -> OutLink:
    return OutLink(
        page_url=page,
        url=f"https://{root}/",
        target_host=root,
        target_root=root,
        anchor="Buy now",
        anchor_key="buy now",
        nofollow=False,
        sponsored=True,
        ugc=False,
    )


def test_selling_pages_count_a_showcase_once() -> None:
    """Витрина с двумя рекламодателями — одна страница; спорный страниц не добавляет."""
    links = [
        _outlink("https://donor.test/a", "one.test"),
        _outlink("https://donor.test/a", "two.test"),
        _outlink("https://donor.test/b", "two.test"),
        _outlink("https://donor.test/c", "maybe.test"),
    ]
    candidates = [
        Candidate("one.test", 5, Verdict.BOUGHT),
        Candidate("two.test", 5, Verdict.BOUGHT),
        Candidate("maybe.test", 2, Verdict.PENDING),
    ]

    assert gate.selling_pages(links, candidates) == 2


async def test_judging_a_crawl_keeps_its_selling_pages(session: AsyncSession) -> None:
    """Число кладёт пересчёт кандидатов — настоящим путём, а не фикстурой."""
    run = await crawled(session, DONOR)
    for page in ("a", "b"):
        session.add(
            OutLinkModel(
                crawl_run_id=run.id,
                page_url=f"https://{DONOR}/{page}",
                url="https://advertiser.example.test/?utm_source=x",
                target_host="advertiser.example.test",
                target_root="advertiser.example.test",
                anchor="Buy now",
                anchor_key="buy now",
                sponsored=True,
            )
        )
    await session.flush()

    await gate.judge_run(session, run.id)

    assert (run.stats or {})[gate.SELLING_PAGES] == 2


async def _quality_run(
    session: AsyncSession, host: str, moment: datetime, *, pages: int, selling: int | None
) -> CrawlRunModel:
    run = await _at(session, await crawled(session, host, pages=pages), moment)
    run.stats = {} if selling is None else {gate.SELLING_PAGES: selling}
    await session.flush()
    return run


async def test_quality_of_a_load_is_by_the_last_usable_crawl(session: AsyncSession) -> None:
    """Повтор заменяет итог, неудачный повтор — нет, и два обхода не складываются."""
    await _load(session, [DONOR, OTHER, "idle.example.test"], TODAY)
    await _quality_run(session, DONOR, TODAY + timedelta(hours=1), pages=100, selling=1)
    last = await _quality_run(session, DONOR, TODAY + timedelta(hours=2), pages=300, selling=30)
    await _refused(session, DONOR, TODAY + timedelta(hours=3))
    other = await _quality_run(session, OTHER, TODAY + timedelta(hours=1), pages=100, selling=10)
    await scored(session, last, "one.example.test")
    await scored(session, other, "two.example.test")
    await scored(session, other, "maybe.example.test", verdict=Verdict.PENDING)

    found = await load_quality.page(session, number=1)

    assert found.total == 1
    row = found.rows[0]
    assert (row.kind, row.donors, row.scanned) == (loads.BULK, 3, 2)
    assert (row.pages, row.selling_links, row.selling_pages) == (400, 2, 40)
    assert row.share == pytest.approx(0.1)
    assert (row.file_name, row.host) == ("party.xlsx", None)


async def test_a_manual_load_shows_its_host(session: AsyncSession) -> None:
    await _load(session, [DONOR], TODAY, kind=loads.MANUAL)

    row = (await load_quality.page(session, number=1)).rows[0]

    assert (row.kind, row.host, row.scanned, row.pages) == (loads.MANUAL, DONOR, 0, 0)
    assert row.share is None


async def test_old_crawls_without_the_number_do_not_lie(session: AsyncSession) -> None:
    """Обход до учёта страниц: доля — только по тем, у кого число есть, и не ноль без него."""
    await _load(session, [DONOR, OTHER], TODAY)
    await _quality_run(session, DONOR, TODAY + timedelta(hours=1), pages=1000, selling=None)
    await _quality_run(session, OTHER, TODAY + timedelta(hours=1), pages=100, selling=50)

    row = (await load_quality.page(session, number=1)).rows[0]

    assert row.pages == 1100
    assert row.share == pytest.approx(0.5)

    await session.execute(
        DonorLoadHostModel.__table__.delete().where(DonorLoadHostModel.host == OTHER)
    )
    found = await load_quality.page(session, number=1)
    row = next(row for row in found.rows if row.kind == loads.BULK)

    # Числа нет ни у одного обхода партии: доля пуста, а ноль сказал бы «искали и не нашли».
    assert (row.pages, row.share) == (1000, None)


async def test_crawls_without_a_load_are_a_row_per_moscow_day(session: AsyncSession) -> None:
    """Обход кнопкой — строка дня; обход донора партии после загрузки — в партии, а не там."""
    await _quality_run(session, DONOR, TODAY + timedelta(minutes=5), pages=10, selling=1)
    await _quality_run(session, OTHER, YESTERDAY, pages=20, selling=2)
    await _load(session, ["loaded.example.test"], TODAY)
    await _quality_run(
        session, "loaded.example.test", TODAY + timedelta(hours=1), pages=30, selling=3
    )

    found = await load_quality.page(session, number=1)

    # Строка дня стоит по последнему обходу дня: 02.11 — позже загрузки.
    assert [(row.kind, row.pages) for row in found.rows] == [
        (load_quality.NO_LOAD, 10),
        (loads.BULK, 30),
        (load_quality.NO_LOAD, 20),
    ]
    assert found.rows[0].day == date(2026, 11, 2)
    assert found.rows[2].day == date(2026, 11, 1)
    assert found.rows[0].host == DONOR


async def test_a_crawl_before_the_load_stays_outside_of_it(session: AsyncSession) -> None:
    await _quality_run(session, DONOR, YESTERDAY, pages=10, selling=1)
    await _load(session, [DONOR], TODAY)

    found = await load_quality.page(session, number=1)

    assert [(row.kind, row.pages) for row in found.rows] == [
        (loads.BULK, 0),
        (load_quality.NO_LOAD, 10),
    ]


async def test_rows_of_quality_are_paged_with_a_full_order(session: AsyncSession) -> None:
    for number in range(3):
        await _load(session, [f"donor-{number}.example.test"], TODAY, kind=loads.MANUAL)

    first = await load_quality.page(session, number=1, limit=2)
    second = await load_quality.page(session, number=2, limit=2)

    assert first.total == second.total == 3
    hosts = [row.host for row in (*first.rows, *second.rows)]
    assert hosts == ["donor-2.example.test", "donor-1.example.test", "donor-0.example.test"]


async def test_hosts_of_a_load_are_all_there_with_their_crawls(session: AsyncSession) -> None:
    load = await _load(session, [DONOR, OTHER], TODAY)
    run = await _quality_run(session, DONOR, TODAY + timedelta(hours=1), pages=10, selling=1)
    await scored(session, run, "one.example.test")

    found = await load_quality.hosts_of(session, load_id=load.id, day=None, number=1)

    assert found.total == 2
    assert [(row.host, row.selling_links) for row in found.rows] == [(DONOR, 1), (OTHER, 0)]
    assert found.rows[0].crawl is not None
    assert found.rows[1].crawl is None


async def test_hosts_of_a_day_are_the_crawls_without_a_load(session: AsyncSession) -> None:
    await _quality_run(session, DONOR, TODAY + timedelta(minutes=5), pages=10, selling=1)
    await _quality_run(session, OTHER, YESTERDAY, pages=10, selling=1)

    found = await load_quality.hosts_of(session, load_id=None, day=date(2026, 11, 2), number=1)

    assert [row.host for row in found.rows] == [DONOR]


# --- маршруты ---


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("панель@site.com", role=UserRole.OPERATOR)
    return await sign_in("панель@site.com")


async def test_the_panel_answers_a_viewer_and_refuses_a_stranger(
    client: AsyncClient, token: str, session: AsyncSession
) -> None:
    now = datetime.now(UTC)
    run = await _at(session, await crawled(session, DONOR), now)
    await scored(session, run, "one.example.test")
    load = await _load(session, [DONOR], now - timedelta(minutes=1))
    await session.commit()

    assert (await client.get("/api/advertisers/panel")).status_code == 403
    answer = await client.get("/api/advertisers/panel", headers=bearer(token))

    assert answer.status_code == 200
    body = answer.json()
    assert body["range"] == "today"
    assert body["donors"]["added"] == 1
    assert body["advertisers"]["found"] == 1
    assert body["load"] is None
    assert [row["id"] for row in body["loads"]] == [load.id]

    chosen = await client.get(
        f"/api/advertisers/panel?range=load&load={load.id}", headers=bearer(token)
    )
    assert chosen.json()["load"]["hosts"] == 1


async def test_an_unknown_load_is_404_in_words(client: AsyncClient, token: str) -> None:
    answer = await client.get("/api/advertisers/panel?range=load&load=999", headers=bearer(token))

    assert answer.status_code == 404
    assert "Партии №999 нет" in answer.json()["detail"]


async def test_quality_and_its_hosts_over_the_api(
    client: AsyncClient, token: str, session: AsyncSession
) -> None:
    load = await _load(session, [DONOR], TODAY)
    await session.commit()

    quality = await client.get("/api/advertisers/panel/quality", headers=bearer(token))
    hosts = await client.get(
        f"/api/advertisers/panel/quality/hosts?load={load.id}", headers=bearer(token)
    )
    both = await client.get(
        f"/api/advertisers/panel/quality/hosts?load={load.id}&day=2026-11-02",
        headers=bearer(token),
    )
    neither = await client.get("/api/advertisers/panel/quality/hosts", headers=bearer(token))

    assert quality.json()["rows"][0]["load_id"] == load.id
    assert quality.json()["limit"] == load_quality.PAGE_SIZE
    assert hosts.json()["rows"][0] == {"host": DONOR, "crawl": None, "selling_links": 0}
    assert both.status_code == neither.status_code == 422


async def test_the_mailing_mode_answers_for_both_queues(client: AsyncClient, token: str) -> None:
    answer = await client.get("/api/advertisers/panel/mailing", headers=bearer(token))

    assert answer.status_code == 200
    assert set(answer.json()) == {"links", "niche"}
    assert answer.json()["links"]["running"] is False


async def test_uploading_a_file_records_the_load(
    client: AsyncClient, token: str, session: AsyncSession
) -> None:
    """Партия — настоящим путём загрузки: записанные домены, без пропущенных строк."""
    table = "домен,цена,валюта\nexample.com,150,USD\nне-адрес,1,\n"

    answer = await client.post(
        "/api/donors/import",
        headers=bearer(token),
        files={"file": ("party.csv", table.encode("utf-8"), "text/csv")},
        data={"crawl": "true"},
    )

    assert answer.status_code == 200
    load = await session.scalar(select(DonorLoadModel))
    assert load is not None
    assert (load.kind, load.file_name, load.crawl) == (loads.BULK, "party.csv", True)
    hosts = await session.scalars(select(DonorLoadHostModel.host))
    assert hosts.all() == ["example.com"]


async def test_entering_a_donor_by_hand_records_the_load(
    client: AsyncClient, token: str, session: AsyncSession
) -> None:
    answer = await client.post(
        "/api/donors",
        headers=bearer(token),
        json={"host": "https://www.example.com/page", "price": "120", "currency": "USD"},
    )

    assert answer.status_code == 200, answer.text
    load = await session.scalar(select(DonorLoadModel))
    assert load is not None
    assert load.kind == loads.MANUAL
    hosts = await session.scalars(select(DonorLoadHostModel.host))
    assert hosts.all() == ["example.com"]


# --- миграция ---


def _down_and_up(connection: Connection) -> tuple[bool, bool]:
    def present() -> bool:
        return bool(connection.scalar(text("SELECT to_regclass('donor_load_hosts') IS NOT NULL")))

    migration = load_migration("b7d4e2a91c36_donor_loads.py")
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = present()
        migration.upgrade()
    return down, present()


async def test_the_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    assert await connection.run_sync(_down_and_up) == (False, True)
