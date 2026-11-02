"""Адрес со страницы бывает любой строкой: дальше обхода идёт только доменное имя.

Обход с боевыми страницами падал на записи: хост длиннее 255 знаков не помещался в колонку,
и донор не кончался никогда. Короткий мусор становился рекламодателем, а одно такое имя в
пакете DR получало отказ Ahrefs на весь пакет — без DR оставались настоящие домены.

Половина проверок здесь — про обратную ошибку: настоящий домен, потерянный правилом, не виден
нигде. Поэтому `example.<суффикс>` проверяется по каждому суффиксу вшитого списка, включая
зоны письменностей, где гласная — знак, а не буква.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.config import ahrefs as ahrefs_cfg
from backend.config import crawl as crawl_cfg
from backend.config import storage
from backend.features.core.domain import CrawlOutcome, CrawlStatus, Verdict
from backend.features.core.models.advertiser import CandidateMemoryModel, CandidateModel
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.crawl.affiliate import affiliation
from backend.features.crawl.links import harvest
from backend.features.crawl.progress import Checkpoint, CrawlInterruptedError, Progress
from backend.features.crawl.repository import queue_crawl
from backend.features.donors.host import _extract, domain_name
from backend.workers import crawl_jobs
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN
from tests.migration_helpers import load_migration
from tests.test_advertisers_cli import _FakeAhrefs
from tests.test_crawl_progress import MAP_ROBOTS, _article, _Sink
from tests.test_crawl_progress import _crawl as _walk
from tests.test_crawl_walk import HOST, FakeSite, _urlset
from tests.test_send_race import committed_sessions

PAGE = f"https://{HOST}/post/1"

#: Строки, снятые с настоящих страниц (телефон выдуман той же формы), и то, что им родня.
NOT_NAMES = [
    "tipster",
    "https",
    "http",
    "v",
    "a",
    "www",
    "google",
    "localhost",
    "'",
    "'+gw+'",
    "+70000000000",
    "futebol%20base",
    "so schlecht sieht das ninten",
    "x" * 256,
    "x" * 300 + ".example.test",
    f"{'y' * 64}.example.test",
    f"{'y.' * 120}zzexample.test",  # 254 знака
    "-leading.example.test",
    "trailing-.example.test",
    ".example.test",
    "example..test",
    "bad char.example.test",
    "v1.2",
    "10.0.0.1",
    "",
    ".",
]

#: Настоящие формы: новые зоны, письменности, punycode, подчёркивание, длинные поддомены.
NAMES = [
    "example.test",
    "brand.newzonetest",
    "www.sub.example.co.uk",
    "my_site.example.test",
    "1.example.test",
    "123.example",
    "пример.рф",
    "xn--e1afmkfd.xn--p1ai",
    "例え.テスト",
    "पुलिस.भारत",
    "บริษัท.ไทย",
    f"{'y' * 63}.example.test",
    f"{'y.' * 120}zexample.test",  # 253 знака, самое длинное имя
]


class TestDomainName:
    @pytest.mark.parametrize("host", NOT_NAMES)
    def test_not_a_name(self, host: str) -> None:
        assert domain_name(host) is None

    @pytest.mark.parametrize("host", NAMES)
    def test_a_name_stays_as_it_is(self, host: str) -> None:
        assert domain_name(host) == host

    def test_the_final_dot_goes(self) -> None:
        assert domain_name("example.test.") == "example.test"

    def test_every_public_suffix_passes(self) -> None:
        """Потерянный настоящий домен не виден нигде, поэтому здесь весь список суффиксов: в нём
        сотни зон не латиницей, и в десятках слов гласная — знак (категория M), а не буква."""
        suffixes = [row.removeprefix("*.").removeprefix("!") for row in _extract.tlds]

        lost = [row for row in suffixes if domain_name(f"example.{row}") is None]

        assert len(suffixes) > 9_000
        assert lost == []


class TestHarvest:
    @pytest.mark.parametrize("host", [row for row in NOT_NAMES if row not in ("10.0.0.1", "", ".")])
    def test_junk_host_is_counted_and_not_a_link(self, host: str) -> None:
        junk: set[str] = set()

        found = harvest(_article(f"http://{host}/x", "https://adv.example/"), PAGE, HOST, junk=junk)

        assert [link.target_root for link in found] == ["adv.example"]
        assert junk == {f"http://{host}/x"}

    def test_junk_outside_the_article_counts_once(self) -> None:
        """Проходов два — тело и страница целиком: мусор в обоих считается один раз, а вне
        статьи не теряется."""
        html = _article("http://tipster/x").replace(
            "</body>", '<div><a href="http://tipster/x">a</a><a href="http://v/">b</a></div></body>'
        )
        junk: set[str] = set()

        assert harvest(html, PAGE, HOST, junk=junk) == []
        assert junk == {"http://tipster/x", "http://v/"}

    @pytest.mark.parametrize(
        "href",
        [
            "mailto:editor@example.test",
            "tel:+70000000000",
            "javascript:void(0)",
            "/same/site",
            "#top",
            "http://10.0.0.1/",
            "http://[::1]/",
            "http://[broken",
        ],
    )
    def test_not_a_web_address_is_not_junk(self, href: str) -> None:
        """Чужая схема и адрес по IP отсеивались и раньше: счёт — только про мусор вместо имени."""
        junk: set[str] = set()

        assert harvest(_article(href), PAGE, HOST, junk=junk) == []
        assert junk == set()

    def test_without_a_collector_junk_is_dropped_all_the_same(self) -> None:
        assert harvest(_article("http://tipster/x"), PAGE, HOST) == []

    @pytest.mark.parametrize(
        ("href", "root", "guessed"),
        [
            ("https://brand.newzonetest/", "brand.newzonetest", True),
            ("https://пример.рф/", "пример.рф", False),
            ("https://xn--e1afmkfd.xn--p1ai/", "xn--e1afmkfd.xn--p1ai", False),
            ("https://my_site.example.co.uk/", "example.co.uk", False),
        ],
    )
    def test_unknown_zones_and_scripts_stay(self, href: str, root: str, guessed: bool) -> None:
        """Правило модуля: домен с неизвестным суффиксом не выбрасывается, а помечается."""
        junk: set[str] = set()

        found = harvest(_article(href), PAGE, HOST, junk=junk)

        assert [(link.target_root, link.root_guessed) for link in found] == [(root, guessed)]
        assert junk == set()

    def test_the_final_dot_is_not_a_new_host(self) -> None:
        """Точка в конце — тот же домен: и у рекламодателя, и у самого донора."""
        found = harvest(
            _article("https://adv.example./offer", f"https://{HOST}./own"), PAGE, HOST, junk=set()
        )

        assert [(link.target_host, link.target_root) for link in found] == [
            ("adv.example", "adv.example")
        ]


class TestNetwork:
    def test_junk_in_a_network_parameter_is_not_an_advertiser(self) -> None:
        """Вторая дорога мусора: хост из параметра сети партнёрок становился кандидатом,
        минуя разбор ссылки."""
        found = affiliation("https://www.awin1.com/cread.php?ued=http%3A%2F%2Ftipster%2Fx")

        assert found is not None
        assert (found.network, found.target_root) == ("awin1.com", None)

    def test_the_next_parameter_can_still_name_it(self) -> None:
        found = affiliation("https://www.awin1.com/cread.php?r=http://tipster/&u=https://adv.test/")

        assert found is not None
        assert found.target_root == "adv.test"

    def test_a_trailing_dot_in_a_parameter_goes_too(self) -> None:
        found = affiliation("https://www.awin1.com/cread.php?ued=https://adv.test./x")

        assert found is not None
        assert found.target_host == "adv.test"


def _junk_site(*extra: str) -> FakeSite:
    """Донор с картой: на каждой странице мусор своего адреса и настоящая ссылка."""
    pages = {
        f"/p{n}": _article(f"https://adv{n}.example/offer", f"http://junk{n}/x") for n in range(4)
    }
    pages["/"] = _article("https://home-adv.example/", *extra)
    return FakeSite(
        pages, robots=MAP_ROBOTS, sitemap=_urlset(*[f"https://{HOST}/p{n}" for n in range(4)])
    )


class TestCount:
    async def test_the_report_counts_what_was_dropped(self) -> None:
        report = await _walk(_junk_site(), None)

        assert report.as_dict()["junk_links"] == 4
        assert all(link.target_root.startswith(("adv", "home")) for link in report.links)

    async def test_the_count_survives_a_resume(self) -> None:
        """Обход идёт несколькими попытками: счёт — в чекпоинте, как у кнопок «поделиться»."""
        asked = iter([False, False, True])
        with pytest.raises(CrawlInterruptedError) as stopped:
            await _walk(
                _junk_site(), Progress(flush=_Sink(), every_pages=100, stop=lambda: next(asked))
            )
        point = stopped.value.checkpoint

        report = await _walk(_junk_site(), Progress(flush=_Sink(), resume=point))

        assert point.junk_links == 2
        assert report.junk_links == 4

    def test_a_checkpoint_from_before_the_count_still_reads(self) -> None:
        """Обход, начатый до выкатки, продолжается: в его чекпоинте счёта нет."""
        saved = Checkpoint("sitemap").as_dict()
        del saved["junk_links"]

        point = Checkpoint.of(saved)

        assert point is not None
        assert point.junk_links == 0


@pytest.fixture
def _wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Задача обхода — на тестовой базе, очередь в памяти: живую забрали бы старые воркеры."""
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr(crawl_cfg, "DELAY_SEC", 0.0)
    monkeypatch.setattr(crawl_cfg, "BROWSER_ENABLED", False)
    monkeypatch.setattr(crawl_jobs, "quick_queue", _Quiet)
    monkeypatch.setattr(ahrefs_cfg, "API_KEY", "test-key")
    monkeypatch.setattr(crawl_jobs, "AhrefsClient", _FakeAhrefs)
    _FakeAhrefs.asked = []
    crawl_jobs._STOP.clear()
    yield
    crawl_jobs._STOP.clear()


class _Quiet:
    def enqueue(self, *_: Any, **__: Any) -> Any:
        return type("Job", (), {"id": "job"})()


#: Имя длиннее колонки: на нём обход падал посреди записи.
TOO_LONG = "l" * 300

#: Мусор с «куплено»: у ссылки `rel=sponsored`, и без отсева имя ушло бы в пакет DR.
BOUGHT = [
    f"http://{TOO_LONG}/x",
    "http://tipster/x",
    "http://'+gw+'/",
    "http://+70000000000/",
    "https://www.awin1.com/cread.php?ued=http%3A%2F%2Fnodot%2Fx",
]


def _sponsored_site(monkeypatch: pytest.MonkeyPatch) -> FakeSite:
    page = _article("https://small-brand.example/offer", *BOUGHT)
    site = FakeSite({"/": page.replace('href="', 'rel="sponsored" href="')})

    def client(**_: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(site.handler))

    monkeypatch.setattr(crawl_jobs, "guarded_client", client)
    return site


@pytest.mark.usefixtures("_wired")
async def test_junk_reaches_neither_the_record_nor_the_dr_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Задача целиком, на настоящей базе: обход кончается, число в строке обхода верное, в
    ссылках и кандидатах только домены, и в пакет DR не уходит ни одно чужое имя."""
    _sponsored_site(monkeypatch)
    async with committed_sessions() as factory:
        async with factory() as session:
            run = await queue_crawl(session, HOST, by="op@t.test")
            run.job_id = "first"
            await session.commit()
            run_id = run.id

        done = await crawl_jobs._crawl(run_id)
        await crawl_jobs._judge(run_id)

        async with factory() as session:
            row = await session.get(CrawlRunModel, run_id)
            hosts = list((await session.execute(select(OutLinkModel.target_root))).scalars())
            roots = list((await session.execute(select(CandidateModel.target_root))).scalars())

    assert row is not None
    assert done["outcome"] == CrawlOutcome.OK.value
    assert (row.status, row.outcome) == (CrawlStatus.DONE, CrawlOutcome.OK)
    assert row.stats is not None
    assert row.stats["junk_links"] == 4, "параметр сети — не ссылка: ссылка на сеть настоящая"
    assert sorted(hosts) == ["awin1.com", "small-brand.example"]
    assert "nodot" not in roots
    assert all("." in root for root in roots)
    asked = [host for batch in _FakeAhrefs.asked for host in batch]
    assert "small-brand.example" in asked
    assert all("." in host for host in asked)


MIGRATION = "c5d30109d388_drop_junk_link_hosts.py"

#: Что миграция обязана оставить: настоящие формы, новая зона, ссылка с негодным хостом, но
#: настоящим корнем — её кандидат не мусор.
KEPT_ROOTS = {"adv.example", "пример.рф", "my_site.example.test", "brand.newzonetest"}


async def _stored(session: AsyncSession) -> None:
    """Обход донора с настоящими и мусорными строками во всех трёх таблицах."""
    run = CrawlRunModel(host=HOST, status=CrawlStatus.DONE)
    session.add(run)
    await session.flush()
    hosts = [(host, host) for host in sorted(KEPT_ROOTS)]
    hosts += [("www.adv.example.", "adv.example"), ("bad char.adv.example", "adv.example")]
    hosts += [(host, host) for host in ("tipster", "'+gw+'", "+70000000000", "x" * 200)]
    session.add_all(
        OutLinkModel(
            crawl_run_id=run.id,
            page_url=PAGE,
            url=f"http://{host}/",
            target_host=host,
            target_root=root,
        )
        for host, root in hosts
    )
    for root in (*sorted(KEPT_ROOTS), "tipster", "https", "'+gw+'"):
        session.add(
            CandidateModel(
                crawl_run_id=run.id, donor_host=HOST, target_root=root, verdict=Verdict.PENDING
            )
        )
        session.add(CandidateMemoryModel(donor_host=HOST, target_root=root, confirmed=True))
    await session.flush()


def _run_migration(connection: Connection) -> None:
    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        migration.upgrade()
        migration.upgrade()


class TestCleanup:
    async def test_the_revision_takes_junk_and_only_junk(self, session: AsyncSession) -> None:
        await _stored(session)
        connection = await session.connection()

        await connection.run_sync(_run_migration)

        hosts = set((await session.execute(select(OutLinkModel.target_host))).scalars())
        roots = set((await session.execute(select(CandidateModel.target_root))).scalars())
        memory = set((await session.execute(select(CandidateMemoryModel.target_root))).scalars())
        assert hosts == KEPT_ROOTS | {"www.adv.example."}
        assert roots == KEPT_ROOTS
        assert memory == KEPT_ROOTS

    async def test_batches_cover_a_long_list(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Имён бывает больше пачки: пачка в одно имя обязана удалить всё то же."""
        await _stored(session)
        migration = load_migration(MIGRATION)
        monkeypatch.setattr(migration, "BATCH", 1)

        def clean(connection: Connection) -> None:
            migration._clean(connection, "outlinks", "target_host")

        await (await session.connection()).run_sync(clean)

        hosts = set((await session.execute(select(OutLinkModel.target_host))).scalars())
        assert hosts == KEPT_ROOTS | {"www.adv.example."}

    @pytest.mark.parametrize("host", [*NOT_NAMES, *NAMES, "example.test."])
    def test_the_copy_of_the_rule_says_the_same(self, host: str) -> None:
        """Копия в миграции — намеренно (шапка ревизии), и сегодня она обязана совпадать."""
        assert load_migration(MIGRATION)._is_name(host) is (domain_name(host) is not None)
